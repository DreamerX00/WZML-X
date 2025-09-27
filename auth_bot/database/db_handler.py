#!/usr/bin/env python3
# Auth Bot - Database Handler

import logging
import asyncio
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Any
import motor.motor_asyncio
from pymongo.errors import DuplicateKeyError, PyMongoError

from auth_bot import DATABASE_URL
from auth_bot.db_models import (
    User, Subscription, Payment, Token, UsageStats, Analytics,
    PaymentStatus, SubscriptionStatus, COLLECTIONS, to_dict, from_dict
)

logger = logging.getLogger(__name__)

class DBManager:
    def __init__(self):
        self.client = None
        self.db = None
        self._connected = False
    
    async def connect(self):
        """Connect to MongoDB database"""
        try:
            # Connect to MongoDB with proper configuration
            self.client = motor.motor_asyncio.AsyncIOMotorClient(
                DATABASE_URL,
                serverSelectionTimeoutMS=10000,
                connectTimeoutMS=10000,
                socketTimeoutMS=10000,
                maxPoolSize=10,
                retryWrites=True
            )
            
            # Get database - check if database name is in URL
            if '/auth_bot?' in DATABASE_URL:
                # Database name is specified in URL
                self.db = self.client.get_database()
            else:
                # Use default database name
                self.db = self.client.auth_bot
            
            # Test connection
            await self.client.admin.command('ping')
            self._connected = True
            
            # Setup indexes
            await self._setup_indexes()
            
            logger.info(f"Connected to MongoDB database '{self.db.name}' successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to MongoDB: {e}")
            return False
    
    async def _setup_indexes(self):
        """Setup database indexes for optimal performance"""
        try:
            for collection_name, config in COLLECTIONS.items():
                collection = self.db[collection_name]
                
                for index_config in config.get('indexes', []):
                    keys = index_config['keys']
                    options = {k: v for k, v in index_config.items() if k != 'keys'}
                    
                    try:
                        await collection.create_index(keys, **options)
                    except DuplicateKeyError:
                        pass  # Index already exists
            
            logger.info("Database indexes setup completed")
        except Exception as e:
            logger.error(f"Error setting up indexes: {e}")
    
    async def disconnect(self):
        """Disconnect from MongoDB"""
        if self.client:
            self.client.close()
            self._connected = False
            logger.info("Disconnected from MongoDB")
    
    # User Management
    async def add_user(self, user_id: int, username: str, first_name: str, last_name: str = None) -> bool:
        """Add a new user to the database"""
        try:
            user = User(
                user_id=user_id,
                username=username,
                first_name=first_name,
                last_name=last_name
            )
            
            await self.db.users.insert_one(to_dict(user))
            logger.info(f"User {user_id} added successfully")
            return True
        except DuplicateKeyError:
            logger.info(f"User {user_id} already exists")
            return True
        except Exception as e:
            logger.error(f"Error adding user {user_id}: {e}")
            return False
    
    async def get_user(self, user_id: int) -> Optional[User]:
        """Get user by ID"""
        try:
            user_data = await self.db.users.find_one({"user_id": user_id})
            return from_dict(user_data, User) if user_data else None
        except Exception as e:
            logger.error(f"Error getting user {user_id}: {e}")
            return None
    
    async def user_exists(self, user_id: int) -> bool:
        """Check if user exists"""
        try:
            count = await self.db.users.count_documents({"user_id": user_id})
            return count > 0
        except Exception as e:
            logger.error(f"Error checking user existence {user_id}: {e}")
            return False
    
    async def update_user_activity(self, user_id: int):
        """Update user's last active timestamp"""
        try:
            await self.db.users.update_one(
                {"user_id": user_id},
                {"$set": {"last_active": datetime.now()}}
            )
        except Exception as e:
            logger.error(f"Error updating user activity {user_id}: {e}")
    
    async def update_notification_status(self, user_id: int, notification_date: datetime):
        """Update user's last notification timestamp"""
        try:
            await self.db.users.update_one(
                {"user_id": user_id},
                {"$set": {"last_notification": notification_date}}
            )
        except Exception as e:
            logger.error(f"Error updating notification status {user_id}: {e}")
    
    async def get_all_users(self, limit: int = 100) -> List[Dict]:
        """Get all users with pagination"""
        try:
            cursor = self.db.users.find().sort("created_at", -1).limit(limit)
            users = await cursor.to_list(length=limit)
            return users
        except Exception as e:
            logger.error(f"Error getting all users: {e}")
            return []
    
    # Subscription Management
    async def add_subscription(self, user_id: int, plan_type: str, plan_days: int, payment_id: str = None) -> bool:
        """Add a new subscription"""
        try:
            start_date = datetime.now()
            end_date = start_date + timedelta(days=plan_days)
            
            # Check if user already has an active subscription
            existing_subscription = await self.get_user_subscription(user_id)
            if existing_subscription:
                # Update existing subscription instead of creating new one
                return await self.extend_subscription(user_id, plan_days)
            
            subscription = Subscription(
                user_id=user_id,
                plan_type=plan_type,
                plan_days=plan_days,
                start_date=start_date,
                end_date=end_date,
                payment_id=payment_id
            )
            
            await self.db.subscriptions.insert_one(to_dict(subscription))
            logger.info(f"Subscription added for user {user_id}: {plan_type} ({plan_days} days)")
            return True
        except Exception as e:
            logger.error(f"Error adding subscription for user {user_id}: {e}")
            return False
    
    async def get_user_subscription(self, user_id: int) -> Optional[Subscription]:
        """Get user's current subscription"""
        try:
            sub_data = await self.db.subscriptions.find_one(
                {"user_id": user_id, "status": SubscriptionStatus.ACTIVE.value},
                sort=[("created_at", -1)]
            )
            return from_dict(sub_data, Subscription) if sub_data else None
        except Exception as e:
            logger.error(f"Error getting subscription for user {user_id}: {e}")
            return None
    
    async def update_subscription(self, user_id: int, is_active: bool = True, status: str = None):
        """Update subscription status"""
        try:
            update_data = {"updated_at": datetime.now()}
            
            if status:
                update_data["status"] = status
            elif not is_active:
                update_data["status"] = SubscriptionStatus.EXPIRED.value
            
            await self.db.subscriptions.update_one(
                {"user_id": user_id, "status": SubscriptionStatus.ACTIVE.value},
                {"$set": update_data}
            )
            return True
        except Exception as e:
            logger.error(f"Error updating subscription for user {user_id}: {e}")
            return False
    
    async def get_expired_subscriptions(self) -> List[Dict]:
        """Get all expired subscriptions that need processing"""
        try:
            cursor = self.db.subscriptions.find({
                "status": SubscriptionStatus.ACTIVE.value,
                "end_date": {"$lt": datetime.now()}
            })
            return await cursor.to_list(length=None)
        except Exception as e:
            logger.error(f"Error getting expired subscriptions: {e}")
            return []
    
    async def extend_subscription(self, user_id: int, additional_days: int) -> bool:
        """Extend an existing subscription by additional days"""
        try:
            current_subscription = await self.get_user_subscription(user_id)
            if not current_subscription:
                return False
            
            # Calculate new end date
            current_end_date = current_subscription.end_date
            new_end_date = current_end_date + timedelta(days=additional_days)
            
            # Update subscription
            await self.db.subscriptions.update_one(
                {"user_id": user_id, "status": SubscriptionStatus.ACTIVE.value},
                {"$set": {
                    "end_date": new_end_date,
                    "plan_days": current_subscription.plan_days + additional_days,
                    "updated_at": datetime.now()
                }}
            )
            
            logger.info(f"Extended subscription for user {user_id} by {additional_days} days")
            return True
        except Exception as e:
            logger.error(f"Error extending subscription for user {user_id}: {e}")
            return False
    
    # Payment Management
    async def add_payment(self, payment_data: Dict) -> bool:
        """Add a new payment record"""
        try:
            payment = Payment(
                payment_id=payment_data["payment_id"],
                user_id=payment_data["user_id"],
                plan_type=payment_data["plan_type"],
                plan_days=payment_data["plan_days"],
                amount=payment_data["amount"],
                currency=payment_data.get("currency", "USD"),
                payment_method=payment_data["payment_method"]
            )
            
            await self.db.payments.insert_one(to_dict(payment))
            logger.info(f"Payment {payment_data['payment_id']} added successfully")
            return True
        except Exception as e:
            logger.error(f"Error adding payment: {e}")
            return False
    
    async def get_payment(self, payment_id: str) -> Optional[Dict]:
        """Get payment by ID"""
        try:
            return await self.db.payments.find_one({"payment_id": payment_id})
        except Exception as e:
            logger.error(f"Error getting payment {payment_id}: {e}")
            return None
    
    async def update_payment_status(self, payment_id: str, status: str, gateway_payment_id: str = None) -> bool:
        """Update payment status"""
        try:
            update_data = {
                "status": status,
                "updated_at": datetime.now()
            }
            
            if gateway_payment_id:
                update_data["gateway_payment_id"] = gateway_payment_id
            
            result = await self.db.payments.update_one(
                {"payment_id": payment_id},
                {"$set": update_data}
            )
            return result.modified_count > 0
        except Exception as e:
            logger.error(f"Error updating payment status {payment_id}: {e}")
            return False
    
    # Token Management
    async def add_token(self, token: str, user_id: int, expires_at: datetime) -> bool:
        """Add a new token"""
        try:
            token_obj = Token(
                token=token,
                user_id=user_id,
                plan_days=0,  # Will be set when used
                expires_at=expires_at
            )
            
            await self.db.tokens.insert_one(to_dict(token_obj))
            return True
        except Exception as e:
            logger.error(f"Error adding token: {e}")
            return False
    
    async def get_token(self, token: str) -> Optional[Dict]:
        """Get token by value"""
        try:
            return await self.db.tokens.find_one({"token": token})
        except Exception as e:
            logger.error(f"Error getting token: {e}")
            return None
    
    async def use_token(self, token: str) -> bool:
        """Mark token as used"""
        try:
            result = await self.db.tokens.update_one(
                {"token": token},
                {"$set": {"used": True, "used_at": datetime.now()}}
            )
            return result.modified_count > 0
        except Exception as e:
            logger.error(f"Error using token: {e}")
            return False
    
    # Usage Analytics
    async def log_command_usage(self, user_id: int, command: str, success: bool = True, 
                               response_time: float = None, error_message: str = None):
        """Log command usage for analytics"""
        try:
            usage = UsageStats(
                user_id=user_id,
                command=command,
                timestamp=datetime.now(),
                success=success,
                response_time=response_time,
                error_message=error_message
            )
            
            await self.db.usage_stats.insert_one(to_dict(usage))
        except Exception as e:
            logger.error(f"Error logging command usage: {e}")
    
    async def get_usage_stats(self, days: int = 30) -> Dict[str, Any]:
        """Get usage statistics for the last N days"""
        try:
            start_date = datetime.now() - timedelta(days=days)
            
            pipeline = [
                {"$match": {"timestamp": {"$gte": start_date}}},
                {"$group": {
                    "_id": "$command",
                    "total_uses": {"$sum": 1},
                    "successful_uses": {"$sum": {"$cond": ["$success", 1, 0]}},
                    "failed_uses": {"$sum": {"$cond": ["$success", 0, 1]}},
                    "avg_response_time": {"$avg": "$response_time"}
                }},
                {"$sort": {"total_uses": -1}}
            ]
            
            cursor = self.db.usage_stats.aggregate(pipeline)
            stats = await cursor.to_list(length=None)
            
            return {
                "period_days": days,
                "command_stats": stats,
                "total_commands": sum(stat["total_uses"] for stat in stats)
            }
        except Exception as e:
            logger.error(f"Error getting usage stats: {e}")
            return {}
    
    # Analytics Dashboard Data
    async def get_payment_analytics(self, days: int = 30) -> Dict[str, Any]:
        """Get payment analytics for dashboard"""
        try:
            start_date = datetime.now() - timedelta(days=days)
            
            # Payment statistics
            payment_pipeline = [
                {"$match": {"created_at": {"$gte": start_date}}},
                {"$group": {
                    "_id": "$status",
                    "count": {"$sum": 1},
                    "total_amount": {"$sum": "$amount"}
                }}
            ]
            
            payment_stats = await self.db.payments.aggregate(payment_pipeline).to_list(length=None)
            
            # Payment method breakdown
            method_pipeline = [
                {"$match": {"created_at": {"$gte": start_date}}},
                {"$group": {
                    "_id": "$payment_method",
                    "count": {"$sum": 1},
                    "total_amount": {"$sum": "$amount"},
                    "success_rate": {
                        "$avg": {"$cond": [{"$eq": ["$status", "success"]}, 1, 0]}
                    }
                }}
            ]
            
            method_stats = await self.db.payments.aggregate(method_pipeline).to_list(length=None)
            
            # Daily revenue
            daily_pipeline = [
                {"$match": {
                    "created_at": {"$gte": start_date},
                    "status": "success"
                }},
                {"$group": {
                    "_id": {
                        "$dateToString": {
                            "format": "%Y-%m-%d",
                            "date": "$created_at"
                        }
                    },
                    "revenue": {"$sum": "$amount"},
                    "transactions": {"$sum": 1}
                }},
                {"$sort": {"_id": 1}}
            ]
            
            daily_revenue = await self.db.payments.aggregate(daily_pipeline).to_list(length=None)
            
            return {
                "period_days": days,
                "payment_status": payment_stats,
                "payment_methods": method_stats,
                "daily_revenue": daily_revenue
            }
        except Exception as e:
            logger.error(f"Error getting payment analytics: {e}")
            return {}
    
    async def get_subscription_analytics(self, days: int = 30) -> Dict[str, Any]:
        """Get subscription analytics for dashboard"""
        try:
            start_date = datetime.now() - timedelta(days=days)
            
            # Subscription statistics
            sub_pipeline = [
                {"$match": {"created_at": {"$gte": start_date}}},
                {"$group": {
                    "_id": "$plan_type",
                    "count": {"$sum": 1},
                    "active_count": {
                        "$sum": {"$cond": [{"$eq": ["$status", "active"]}, 1, 0]}
                    }
                }}
            ]
            
            sub_stats = await self.db.subscriptions.aggregate(sub_pipeline).to_list(length=None)
            
            # Active subscriptions by plan
            active_subs = await self.db.subscriptions.count_documents({
                "status": SubscriptionStatus.ACTIVE.value,
                "end_date": {"$gt": datetime.now()}
            })
            
            # Expiring soon (next 7 days)
            expiring_soon = await self.db.subscriptions.count_documents({
                "status": SubscriptionStatus.ACTIVE.value,
                "end_date": {
                    "$gt": datetime.now(),
                    "$lt": datetime.now() + timedelta(days=7)
                }
            })
            
            return {
                "period_days": days,
                "subscription_plans": sub_stats,
                "active_subscriptions": active_subs,
                "expiring_soon": expiring_soon
            }
        except Exception as e:
            logger.error(f"Error getting subscription analytics: {e}")
            return {}
    
    async def get_user_analytics(self, days: int = 30) -> Dict[str, Any]:
        """Get user analytics for dashboard"""
        try:
            start_date = datetime.now() - timedelta(days=days)
            
            # Total users
            total_users = await self.db.users.count_documents({})
            
            # New users in period
            new_users = await self.db.users.count_documents({
                "created_at": {"$gte": start_date}
            })
            
            # Active users (used bot in last 24 hours)
            active_users = await self.db.users.count_documents({
                "last_active": {"$gte": datetime.now() - timedelta(hours=24)}
            })
            
            # Daily new users
            daily_pipeline = [
                {"$match": {"created_at": {"$gte": start_date}}},
                {"$group": {
                    "_id": {
                        "$dateToString": {
                            "format": "%Y-%m-%d",
                            "date": "$created_at"
                        }
                    },
                    "new_users": {"$sum": 1}
                }},
                {"$sort": {"_id": 1}}
            ]
            
            daily_users = await self.db.users.aggregate(daily_pipeline).to_list(length=None)
            
            return {
                "period_days": days,
                "total_users": total_users,
                "new_users": new_users,
                "active_users": active_users,
                "daily_new_users": daily_users
            }
        except Exception as e:
            logger.error(f"Error getting user analytics: {e}")
            return {}
    
    async def generate_daily_analytics(self, date: datetime = None) -> bool:
        """Generate and store daily analytics summary"""
        try:
            if date is None:
                date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            
            # Get analytics for the day
            start_of_day = date
            end_of_day = date + timedelta(days=1)
            
            # User metrics
            total_users = await self.db.users.count_documents({})
            new_users = await self.db.users.count_documents({
                "created_at": {"$gte": start_of_day, "$lt": end_of_day}
            })
            active_users = await self.db.users.count_documents({
                "last_active": {"$gte": start_of_day, "$lt": end_of_day}
            })
            
            # Subscription metrics
            total_subs = await self.db.subscriptions.count_documents({})
            active_subs = await self.db.subscriptions.count_documents({
                "status": SubscriptionStatus.ACTIVE.value,
                "end_date": {"$gt": datetime.now()}
            })
            expired_subs = await self.db.subscriptions.count_documents({
                "status": SubscriptionStatus.EXPIRED.value
            })
            
            # Payment metrics
            total_payments = await self.db.payments.count_documents({
                "created_at": {"$gte": start_of_day, "$lt": end_of_day}
            })
            successful_payments = await self.db.payments.count_documents({
                "created_at": {"$gte": start_of_day, "$lt": end_of_day},
                "status": PaymentStatus.SUCCESS.value
            })
            failed_payments = await self.db.payments.count_documents({
                "created_at": {"$gte": start_of_day, "$lt": end_of_day},
                "status": PaymentStatus.FAILED.value
            })
            
            # Revenue calculation
            revenue_pipeline = [
                {"$match": {
                    "created_at": {"$gte": start_of_day, "$lt": end_of_day},
                    "status": PaymentStatus.SUCCESS.value
                }},
                {"$group": {"_id": None, "total_revenue": {"$sum": "$amount"}}}
            ]
            
            revenue_result = await self.db.payments.aggregate(revenue_pipeline).to_list(length=1)
            revenue = revenue_result[0]["total_revenue"] if revenue_result else 0.0
            
            # Command usage
            commands_executed = await self.db.usage_stats.count_documents({
                "timestamp": {"$gte": start_of_day, "$lt": end_of_day}
            })
            
            # Create analytics record
            analytics = Analytics(
                date=date,
                total_users=total_users,
                active_users=active_users,
                new_users=new_users,
                total_subscriptions=total_subs,
                active_subscriptions=active_subs,
                expired_subscriptions=expired_subs,
                total_payments=total_payments,
                successful_payments=successful_payments,
                failed_payments=failed_payments,
                revenue=revenue,
                commands_executed=commands_executed
            )
            
            # Upsert analytics record
            await self.db.analytics.update_one(
                {"date": date},
                {"$set": to_dict(analytics)},
                upsert=True
            )
            
            logger.info(f"Daily analytics generated for {date.strftime('%Y-%m-%d')}")
            return True
        except Exception as e:
            logger.error(f"Error generating daily analytics: {e}")
            return False
    
    async def get_analytics_summary(self, days: int = 30) -> Dict[str, Any]:
        """Get comprehensive analytics summary"""
        try:
            start_date = datetime.now() - timedelta(days=days)
            
            # Get stored analytics
            cursor = self.db.analytics.find(
                {"date": {"$gte": start_date}}
            ).sort("date", -1)
            
            analytics_data = await cursor.to_list(length=None)
            
            if not analytics_data:
                # Generate analytics if none exist
                await self.generate_daily_analytics()
                analytics_data = await cursor.to_list(length=1)
            
            # Calculate totals and averages
            total_revenue = sum(a.get("revenue", 0) for a in analytics_data)
            total_new_users = sum(a.get("new_users", 0) for a in analytics_data)
            avg_daily_revenue = total_revenue / len(analytics_data) if analytics_data else 0
            
            return {
                "period_days": days,
                "total_revenue": total_revenue,
                "total_new_users": total_new_users,
                "avg_daily_revenue": avg_daily_revenue,
                "daily_analytics": analytics_data
            }
        except Exception as e:
            logger.error(f"Error getting analytics summary: {e}")
            return {}
    
    # Admin Analytics Methods
    async def get_top_users_by_payments(self, limit: int = 10) -> List[Dict]:
        """Get top users by payment amount"""
        try:
            pipeline = [
                {"$match": {"status": PaymentStatus.SUCCESS.value}},
                {"$group": {
                    "_id": "$user_id",
                    "total_spent": {"$sum": "$amount"},
                    "payment_count": {"$sum": 1}
                }},
                {"$sort": {"total_spent": -1}},
                {"$limit": limit}
            ]
            
            return await self.db.payments.aggregate(pipeline).to_list(length=limit)
        except Exception as e:
            logger.error(f"Error getting top users: {e}")
            return []
    
    async def get_payment_method_performance(self) -> List[Dict]:
        """Get payment method success rates"""
        try:
            pipeline = [
                {"$group": {
                    "_id": "$payment_method",
                    "total_attempts": {"$sum": 1},
                    "successful_payments": {
                        "$sum": {"$cond": [{"$eq": ["$status", "success"]}, 1, 0]}
                    },
                    "total_revenue": {
                        "$sum": {"$cond": [{"$eq": ["$status", "success"]}, "$amount", 0]}
                    }
                }},
                {"$addFields": {
                    "success_rate": {
                        "$multiply": [
                            {"$divide": ["$successful_payments", "$total_attempts"]},
                            100
                        ]
                    }
                }},
                {"$sort": {"success_rate": -1}}
            ]
            
            return await self.db.payments.aggregate(pipeline).to_list(length=None)
        except Exception as e:
            logger.error(f"Error getting payment method performance: {e}")
            return []
