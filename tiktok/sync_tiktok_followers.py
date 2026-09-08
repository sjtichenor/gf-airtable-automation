import os
import json
import requests
import re
from datetime import datetime, timedelta
from dotenv import load_dotenv
import time

# Load environment variables
load_dotenv()

class TikTokFollowersSync:
    def __init__(self):
        self.airtable_token = os.environ['AIRTABLE_PERSONAL_ACCESS_TOKEN']
        self.airtable_base_id = os.getenv('AIRTABLE_BASE_ID',"appxCYu0Tfwc6h7X7")
        self.channels_table_id = os.getenv('AIRTABLE_CHANNELS_TABLE_ID', "tblP8bh3crTHLVoZA")
        
        # TikTok API URLs
        self.tiktok_base_url = "https://open.tiktokapis.com"
        
        # Airtable URLs
        self.channels_table_url = f"https://api.airtable.com/v0/{self.airtable_base_id}/{self.channels_table_id}"
        
        # Headers
        self.airtable_headers = {
            'Authorization': f'Bearer {self.airtable_token}',
            'Content-Type': 'application/json'
        }
        
        # Load TikTok accounts from environment
        tiktok_accounts_json = os.getenv('TIKTOK_ACCOUNTS', '[]')
        try:
            self.tiktok_accounts = json.loads(tiktok_accounts_json)
            print(f"✅ Loaded {len(self.tiktok_accounts)} TikTok accounts from environment")
        except (json.JSONDecodeError, KeyError) as e:
            print(f"❌ Error loading TikTok accounts from environment: {e}")
            self.tiktok_accounts = []
        
        # Runtime mappings
        self.username_to_account = {}
        self.channel_name_to_record = {}

    def refresh_access_token(self, account):
        """Refresh expired access token using refresh token"""
        try:
            print(f"🔄 Refreshing access token for {account.get('channel_name', 'Unknown')}")
            
            url = f"{self.tiktok_base_url}/v2/oauth/token/"
            
            headers = {
                'Content-Type': 'application/x-www-form-urlencoded',
                'Cache-Control': 'no-cache'
            }
            
            data = {
                'client_key': os.getenv('TIKTOK_CLIENT_KEY', ''),
                'client_secret': os.getenv('TIKTOK_CLIENT_SECRET', ''),
                'grant_type': 'refresh_token',
                'refresh_token': account['refresh_token']
            }
            
            response = requests.post(url, headers=headers, data=data)
            print(f"📡 Token refresh response status: {response.status_code}")
            
            if response.status_code == 200:
                token_data = response.json()
                
                if 'access_token' in token_data:
                    account['access_token'] = token_data['access_token']
                    account['refresh_token'] = token_data['refresh_token']
                    account['expires_in'] = token_data.get('expires_in', 86400)
                    account['token_refreshed_at'] = datetime.now().isoformat()
                    
                    print(f"✅ Successfully refreshed access token")
                    return True
                else:
                    print(f"❌ No access token in refresh response: {token_data}")
                    return False
            else:
                print(f"❌ Token refresh failed: {response.status_code}")
                return False
                
        except Exception as e:
            print(f"❌ Error refreshing token: {e}")
            return False

    def is_token_expired(self, account):
        """Check if access token is expired or will expire soon"""
        try:
            if 'token_refreshed_at' in account and 'expires_in' in account:
                refreshed_at = datetime.fromisoformat(account['token_refreshed_at'])
                expires_in_seconds = account['expires_in']
                expires_at = refreshed_at + timedelta(seconds=expires_in_seconds)
                
                buffer_time = timedelta(hours=2)
                return datetime.now() > (expires_at - buffer_time)
            
            return True
            
        except Exception as e:
            print(f"⚠️ Error checking token expiry: {e}")
            return True

    def get_user_info_and_followers(self, account):
        """Get current user info including follower count"""
        try:
            print(f"🔍 Fetching user info for {account.get('channel_name', 'Unknown')}")
            
            url = f"{self.tiktok_base_url}/v2/user/info/"
            
            headers = {
                'Authorization': f"Bearer {account['access_token']}",
            }
            
            params = {
                'fields': 'open_id,display_name,username,follower_count,following_count,likes_count,video_count,is_verified'
            }
            
            response = requests.get(url, headers=headers, params=params)
            print(f"📡 User info API response status: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                
                if data.get('data') and data['data'].get('user'):
                    user_info = data['data']['user']
                    
                    username = user_info.get('username', '')
                    follower_count = user_info.get('follower_count', 0)
                    
                    print(f"✅ User info retrieved:")
                    print(f"   Username: @{username}")
                    print(f"   Display Name: {user_info.get('display_name', 'N/A')}")
                    print(f"   Followers: {follower_count:,}")
                    print(f"   Following: {user_info.get('following_count', 0):,}")
                    print(f"   Total Likes: {user_info.get('likes_count', 0):,}")
                    print(f"   Videos: {user_info.get('video_count', 0):,}")
                    
                    return {
                        'username': username,
                        'display_name': user_info.get('display_name', ''),
                        'follower_count': follower_count,
                        'following_count': user_info.get('following_count', 0),
                        'likes_count': user_info.get('likes_count', 0),
                        'video_count': user_info.get('video_count', 0),
                        'is_verified': user_info.get('is_verified', False)
                    }
                else:
                    print(f"⚠️ No user data in response")
                    return None
            else:
                print(f"❌ User info request failed: {response.status_code}")
                try:
                    error_data = response.json()
                    if error_data.get('error', {}).get('code') == 'scope_not_authorized':
                        print(f"⚠️ Missing user.info.stats scope - reauthorization needed")
                except:
                    pass
                return None
                
        except Exception as e:
            print(f"❌ Error fetching user info: {e}")
            return None

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

    def build_channel_mapping(self):
        """Build mapping of channel names to Airtable record IDs"""
        print(f"📋 Fetching channels from Airtable...")
        
        channels = self.get_all_channels_from_airtable()
        print(f"📊 Found {len(channels)} channels in Airtable")
        
        for channel in channels:
            fields = channel['fields']
            channel_name = fields.get('Social Media Account', '').strip()
            tiktok_profile = fields.get('TikTok Profile', '').strip()
            current_followers = fields.get('TikTok Followers', 0)
            
            if channel_name:
                self.channel_name_to_record[channel_name] = {
                    'record_id': channel['id'],
                    'tiktok_profile': tiktok_profile,
                    'current_followers': current_followers
                }
                print(f"   • {channel_name} → {current_followers:,} followers")
        
        print(f"✅ Mapped {len(self.channel_name_to_record)} channels")

    def build_account_mapping(self):
        """Build runtime mapping of TikTok accounts with user info"""
        print(f"🔄 Building TikTok account mapping...")
        
        for i, account in enumerate(self.tiktok_accounts, 1):
            channel_name = account.get('channel_name', f'Account {i}')
            print(f"\n📱 [{i}/{len(self.tiktok_accounts)}] Processing: {channel_name}")
            
            # Check if token needs refresh
            if self.is_token_expired(account):
                print(f"   🔄 Access token expired, attempting refresh...")
                if not self.refresh_access_token(account):
                    print(f"   ❌ Failed to refresh token, skipping account")
                    continue
            else:
                print(f"   ✅ Access token is valid")
            
            # Get current user info and followers
            user_info = self.get_user_info_and_followers(account)
            if user_info:
                account_data = account.copy()
                account_data.update(user_info)
                
                self.username_to_account[channel_name] = account_data
                print(f"   ✅ Mapped {channel_name} → @{user_info['username']} ({user_info['follower_count']:,} followers)")
            else:
                print(f"   ⚠️ Could not get user info for {channel_name}")
            
            time.sleep(0.5)
        
        print(f"\n📊 Successfully mapped {len(self.username_to_account)} out of {len(self.tiktok_accounts)} accounts")

    def update_channel_followers(self, record_id, follower_count, channel_name):
        """Update channel record with TikTok followers count"""
        try:
            print(f"      💾 Updating Airtable record: {record_id}")
            
            record = {
                'fields': {
                    'TikTok Followers': follower_count
                }
            }
            
            print(f"      📊 TikTok Followers: {follower_count:,}")
            
            url = f"{self.channels_table_url}/{record_id}"
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

    def process_single_channel_followers(self, channel_name, account_data, channel_record):
        """Process follower count for a single channel"""
        record_id = channel_record['record_id']
        current_followers = channel_record['current_followers']
        api_followers = account_data['follower_count']
        
        print(f"🔍 Processing channel: {channel_name}")
        print(f"   TikTok Username: @{account_data['username']}")
        print(f"   Current followers in Airtable: {current_followers:,}")
        print(f"   API followers count: {api_followers:,}")
        
        change = api_followers - current_followers
        if change != 0:
            print(f"   Change: {change:+,}")
        
        success = self.update_channel_followers(record_id, api_followers, channel_name)
        
        if success:
            if change > 0:
                print(f"   ✅ Updated: {api_followers:,} followers (+{change:,})")
            elif change < 0:
                print(f"   ✅ Updated: {api_followers:,} followers ({change:,})")
            else:
                print(f"   ✅ Updated: {api_followers:,} followers (no change)")
            return True
        else:
            print(f"   ❌ Failed to update channel record")
            return False

    def sync_tiktok_followers(self):
        """Main function to sync TikTok follower counts"""
        print("🎵 Starting TikTok Followers Sync...")
        
        # Step 1: Build channel mapping from Airtable
        self.build_channel_mapping()
        
        if not self.channel_name_to_record:
            print("❌ No channels found in Airtable")
            return {'success': 0, 'skip': 0, 'error': 0}
        
        # Step 2: Build account mapping with TikTok API
        if not self.tiktok_accounts:
            print("❌ No TikTok accounts configured")
            return {'success': 0, 'skip': 0, 'error': 0}
        
        self.build_account_mapping()
        
        if not self.username_to_account:
            print("❌ No valid TikTok accounts available")
            print("💡 Tip: Ensure accounts are reauthorized with user.info.stats scope")
            return {'success': 0, 'skip': 0, 'error': 0}
        
        # Step 3: Process each channel
        print(f"\n📊 Processing follower updates...")
        
        success_count = 0
        skip_count = 0
        error_count = 0
        total_followers = 0
        
        for channel_name, account_data in self.username_to_account.items():
            
            # Check if channel exists in Airtable
            if channel_name not in self.channel_name_to_record:
                print(f"⚠️ Channel '{channel_name}' not found in Airtable - skipping")
                skip_count += 1
                continue
            
            channel_record = self.channel_name_to_record[channel_name]
            
            try:
                success = self.process_single_channel_followers(channel_name, account_data, channel_record)
                if success:
                    success_count += 1
                    total_followers += account_data['follower_count']
                else:
                    error_count += 1
                    
            except Exception as e:
                print(f"❌ Unexpected error processing {channel_name}: {e}")
                error_count += 1
            
            time.sleep(0.5)
        
        # Step 4: Summary
        print(f"\n🎉 TikTok followers sync completed!")
        print(f"✅ Successfully updated: {success_count}")
        print(f"⏭️ Skipped (not in Airtable): {skip_count}")
        print(f"❌ Unexpected errors: {error_count}")
        print(f"📊 Total processed: {success_count + skip_count + error_count}")
        
        if success_count > 0:
            print(f"\n📈 Follower Summary:")
            print(f"👥 Total followers across all channels: {total_followers:,}")
            print(f"📊 Average followers per channel: {total_followers // success_count:,}")
        
        if success_count == 0:
            print(f"\n💡 Troubleshooting Tips:")
            print(f"   • Reauthorize TikTok accounts with user.info.stats scope")
            print(f"   • Verify channel names match between environment and Airtable")
            print(f"   • Check that access tokens are valid and not expired")
        
        return {
            'success': success_count,
            'skip': skip_count,
            'error': error_count,
            'total_followers': total_followers
        }

def main():
    sync = TikTokFollowersSync()
    
    # Run TikTok followers sync
    sync.sync_tiktok_followers()

if __name__ == "__main__":
    main()