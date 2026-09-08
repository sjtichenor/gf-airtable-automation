import os
import json
import requests
import re
from datetime import datetime
from dotenv import load_dotenv
import time

# Load environment variables
load_dotenv()

class TwitterFollowersSync:
    def __init__(self):
        self.twitter_bearer_token = os.getenv('TWITTER_BEARER_TOKEN')
        self.airtable_token = os.getenv('AIRTABLE_PERSONAL_ACCESS_TOKEN')
        self.airtable_base_id = os.getenv('AIRTABLE_BASE_ID')
        self.channels_table_id = os.getenv('AIRTABLE_CHANNELS_TABLE_ID', "tblP8bh3crTHLVoZA")
        
        # API URLs
        self.twitter_users_api_url = "https://api.x.com/2/users"
        self.channels_table_url = f"https://api.airtable.com/v0/{self.airtable_base_id}/{self.channels_table_id}"
        
        # Headers
        self.airtable_headers = {
            'Authorization': f'Bearer {self.airtable_token}',
            'Content-Type': 'application/json'
        }
        
        self.twitter_headers = {
            'Authorization': f'Bearer {self.twitter_bearer_token}',
            'User-Agent': 'TwitterFollowersSync/1.0'
        }
        
        # Load Twitter account mapping from environment
        twitter_accounts_json = os.getenv('TWITTER_ACCOUNTS', '[]')
        try:
            twitter_accounts = json.loads(twitter_accounts_json)
            self.username_to_user_id = {
                account['username'].replace('@', ''): account['user_id'] 
                for account in twitter_accounts
            }
        except (json.JSONDecodeError, KeyError) as e:
            print(f"⚠️ Error loading Twitter accounts from environment: {e}")
            # Fallback to hardcoded mapping
            self.username_to_user_id = {
                "goodpolitics__": "1816937077239611392",
                "FreeMBADegree": "1927107449049186305",
                "all_in_tok": "1435359868265828356",
                "bg2clips": "1913065506887892992"
            }

    def get_all_channels_from_airtable(self):
        """Get all channels from Channels table"""
        all_channels = []
        offset = None
        
        while True:
            params = {'pageSize': 100}
            if offset:
                params['offset'] = offset
                
            response = requests.get(self.channels_table_url, 
                                  headers=self.airtable_headers, 
                                  params=params)
            data = response.json()
            
            if 'records' not in data:
                print(f"⚠️ Error fetching channels: {data}")
                break
            
            all_channels.extend(data['records'])
            
            offset = data.get('offset')
            if not offset:
                break
        
        return all_channels

    def extract_twitter_username_from_url(self, twitter_url):
        """Extract Twitter username from profile URL"""
        if not twitter_url:
            return None
            
        try:
            # Handle both twitter.com and x.com domains
            username_match = re.search(r'(?:twitter\.com|x\.com)/([^/?&]+)', twitter_url)
            if username_match:
                return username_match.group(1)
            return None
        except Exception as e:
            print(f"      ⚠️ Error extracting username from {twitter_url}: {e}")
            return None

    def fetch_twitter_follower_counts_batch(self, user_ids):
        """Fetch Twitter follower counts for multiple users in batches"""
        print(f"📡 Fetching follower counts for {len(user_ids)} Twitter accounts...")
        
        # Twitter allows up to 100 user IDs per request
        batch_size = 100
        all_followers = {}
        
        for i in range(0, len(user_ids), batch_size):
            batch_ids = user_ids[i:i + batch_size]
            print(f"   📦 Processing batch {i//batch_size + 1}: {len(batch_ids)} users")
            
            params = {
                "ids": ",".join(batch_ids),
                "user.fields": "public_metrics,username"
            }
            
            try:
                response = requests.get(self.twitter_users_api_url,
                                      headers=self.twitter_headers,
                                      params=params)
                
                print(f"   📡 API Response Status: {response.status_code}")
                
                if response.status_code == 200:
                    data = response.json()
                    
                    if 'data' in data:
                        print(f"   ✅ Successfully retrieved {len(data['data'])} user metrics")
                        
                        for user in data['data']:
                            user_id = user['id']
                            username = user['username']
                            
                            # Extract follower count
                            public_metrics = user.get('public_metrics', {})
                            followers_count = public_metrics.get('followers_count', 0)
                            
                            all_followers[user_id] = {
                                'username': username,
                                'followers_count': followers_count
                            }
                            
                            print(f"      🐦 @{username}: {followers_count:,} followers")
                    
                    if 'errors' in data:
                        print(f"   ⚠️ {len(data['errors'])} users had errors:")
                        for error in data['errors']:
                            user_id = error.get('resource_id', 'unknown')
                            error_detail = error.get('detail', 'Unknown error')
                            print(f"      • User {user_id}: {error_detail}")
                            
                            all_followers[user_id] = {
                                'username': 'unknown',
                                'followers_count': 0,
                                'error': error_detail
                            }
                
                else:
                    print(f"   ❌ Batch request failed: {response.status_code}")
                    print(f"   Response: {response.text}")
                    
                    for user_id in batch_ids:
                        all_followers[user_id] = {
                            'username': 'unknown',
                            'followers_count': 0,
                            'error': f'API Error {response.status_code}'
                        }
            
            except Exception as e:
                print(f"   ❌ Request exception: {e}")
                for user_id in batch_ids:
                    all_followers[user_id] = {
                        'username': 'unknown',
                        'followers_count': 0,
                        'error': str(e)
                    }
            
            # Rate limiting between batches
            time.sleep(2.0)
        
        return all_followers

    def update_channel_twitter_followers(self, channel_record_id, followers_count):
        """Update channel record with Twitter followers count"""
        try:
            print(f"      💾 Updating channel record: {channel_record_id}")
            
            record = {
                'fields': {
                    'Twitter Followers': followers_count
                }
            }
            
            print(f"      📊 Twitter Followers: {followers_count:,}")
            
            url = f"{self.channels_table_url}/{channel_record_id}"
            response = requests.patch(url, 
                                    headers=self.airtable_headers, 
                                    json=record)
            
            print(f"      📡 Airtable update response status: {response.status_code}")
            
            if response.status_code == 200:
                print(f"      ✅ Successfully updated channel followers")
                return True
            else:
                error_data = response.json()
                print(f"      ❌ Airtable update failed: {error_data}")
                return False
                
        except Exception as e:
            print(f"      ❌ Error updating channel record: {e}")
            return False

    def sync_twitter_followers(self):
        """Main function to sync Twitter follower counts"""
        print("🐦 Starting Twitter Followers Sync...")
        print("📋 Fetching all channels from Airtable...")
        
        all_channels = self.get_all_channels_from_airtable()
        print(f"📊 Found {len(all_channels)} total channels")
        
        # Phase 1: Collect all Twitter user IDs for batch processing
        twitter_user_ids = []
        twitter_channel_mapping = {}  # Map user_id -> channel info
        
        print(f"\n📦 Phase 1: Collecting Twitter accounts for batch processing...")
        
        for channel in all_channels:
            fields = channel['fields']
            twitter_profile = fields.get('Twitter Profile', '')
            
            if twitter_profile:
                username = self.extract_twitter_username_from_url(twitter_profile)
                if username:
                    user_id = self.username_to_user_id.get(username)
                    if user_id:
                        twitter_user_ids.append(user_id)
                        twitter_channel_mapping[user_id] = {
                            'username': username,
                            'channel_name': fields.get('Channel', 'Unknown'),
                            'channel_record_id': channel['id']
                        }
                        print(f"   🐦 Added @{username} (ID: {user_id}) from {fields.get('Channel', 'Unknown')}")
                    else:
                        print(f"   ⚠️ No mapping for Twitter @{username}")
        
        if not twitter_user_ids:
            print("❌ No Twitter accounts found to process")
            return
        
        # Phase 2: Batch fetch all Twitter follower counts
        print(f"\n📡 Phase 2: Batch fetching Twitter followers for {len(twitter_user_ids)} accounts...")
        twitter_followers_data = self.fetch_twitter_follower_counts_batch(twitter_user_ids)
        print(f"✅ Retrieved Twitter data for {len(twitter_followers_data)} accounts")
        
        # Phase 3: Update each channel with cached Twitter data
        print(f"\n📝 Phase 3: Updating channel records...")
        
        success_count = 0
        skip_count = 0
        error_count = 0
        total_followers = 0
        
        for i, user_id in enumerate(twitter_user_ids, 1):
            channel_info = twitter_channel_mapping[user_id]
            channel_name = channel_info['channel_name']
            username = channel_info['username']
            record_id = channel_info['channel_record_id']
            
            print(f"\n📱 [{i}/{len(twitter_user_ids)}] Processing: {channel_name} (@{username})")
            
            if user_id in twitter_followers_data:
                follower_data = twitter_followers_data[user_id]
                
                if 'error' in follower_data:
                    print(f"   ⚠️ Skipping due to error: {follower_data['error']}")
                    skip_count += 1
                else:
                    followers_count = follower_data['followers_count']
                    try:
                        success = self.update_channel_twitter_followers(record_id, followers_count)
                        if success:
                            success_count += 1
                            total_followers += followers_count
                            print(f"   ✅ Updated: {followers_count:,} followers")
                        else:
                            error_count += 1
                    except Exception as e:
                        print(f"   ❌ Unexpected error: {e}")
                        error_count += 1
            else:
                print(f"   ⚠️ No follower data found for @{username}")
                skip_count += 1
            
            # Rate limiting between updates
            time.sleep(0.5)
        
        # Summary
        print(f"\n🎉 Twitter followers sync completed!")
        print(f"✅ Successfully updated: {success_count}")
        print(f"⏭️ Skipped (no data/errors): {skip_count}")
        print(f"❌ Update errors: {error_count}")
        print(f"📊 Total processed: {success_count + skip_count + error_count}")
        
        if success_count > 0:
            print(f"\n📈 Twitter Followers Summary:")
            print(f"👥 Total followers across all accounts: {total_followers:,}")
            print(f"📊 Average followers per account: {total_followers // success_count:,}")
        
        # API usage summary
        batch_count = (len(twitter_user_ids) // 100) + 1
        print(f"\n📡 API Usage:")
        print(f"   • Twitter API requests made: {batch_count}")
        print(f"   • Rate limit: 300 requests per 15 minutes")
        print(f"   • Accounts per request: up to 100")

def main():
    sync = TwitterFollowersSync()
    
    # Run Twitter followers sync
    sync.sync_twitter_followers()

if __name__ == "__main__":
    main()