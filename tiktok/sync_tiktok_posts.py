import os
import json
import requests
import re
from datetime import datetime, timedelta
from dotenv import load_dotenv
import time
from urllib.parse import urlparse

# Load environment variables
load_dotenv()

class TikTokSync:
    def __init__(self):
        self.airtable_token = os.environ['AIRTABLE_PERSONAL_ACCESS_TOKEN']
        self.airtable_base_id = os.getenv('AIRTABLE_BASE_ID',"appxCYu0Tfwc6h7X7")
        self.posts_table_id = os.getenv('AIRTABLE_TABLE_ID', "tblMpYJQjbb5yuKfC")
        
        # TikTok API URLs
        self.tiktok_base_url = "https://open.tiktokapis.com"
        
        # Airtable URLs
        self.posts_table_url = f"https://api.airtable.com/v0/{self.airtable_base_id}/{self.posts_table_id}"
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
        
        # Runtime mapping: username -> account_data (built during sync)
        self.username_to_account = {}

    def resolve_short_url(self, short_url):
        """Resolve TikTok short URL to full URL"""
        try:
            print(f"      🔗 Resolving short URL: {short_url}")
            
            # Make HEAD request to follow redirects without downloading content
            response = requests.head(
                short_url, 
                allow_redirects=True, 
                timeout=10,
                headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
                }
            )
            
            # Get the final URL after all redirects
            final_url = response.url
            print(f"      ✅ Resolved to: {final_url}")
            
            # Validate that we got a proper TikTok URL
            if 'tiktok.com' in final_url and '/video/' in final_url:
                return final_url
            else:
                print(f"      ⚠️ Resolved URL doesn't contain expected TikTok video pattern")
                return None
                
        except requests.RequestException as e:
            print(f"      ❌ Error resolving short URL: {e}")
            return None
        except Exception as e:
            print(f"      ❌ Unexpected error resolving URL: {e}")
            return None

    def refresh_access_token(self, account):
        """Refresh expired access token using refresh token"""
        try:
            print(f"🔄 Refreshing access token for account {account.get('channel_name', 'Unknown')}")
            
            url = f"{self.tiktok_base_url}/v2/oauth/token/"
            
            headers = {
                'Content-Type': 'application/x-www-form-urlencoded',
                'Cache-Control': 'no-cache'
            }
            
            data = {
                'client_key': os.getenv('TIKTOK_CLIENT_KEY', ''),  # You'll need to add this
                'client_secret': os.getenv('TIKTOK_CLIENT_SECRET', ''),  # You'll need to add this
                'grant_type': 'refresh_token',
                'refresh_token': account['refresh_token']
            }
            
            response = requests.post(url, headers=headers, data=data)
            print(f"📡 Token refresh response status: {response.status_code}")
            
            if response.status_code == 200:
                token_data = response.json()
                
                if 'access_token' in token_data:
                    # Update account with new tokens (v2 API returns flat structure)
                    account['access_token'] = token_data['access_token']
                    account['refresh_token'] = token_data['refresh_token']
                    account['expires_in'] = token_data.get('expires_in', 86400)
                    account['token_refreshed_at'] = datetime.now().isoformat()
                    
                    print(f"✅ Successfully refreshed access token")
                    print(f"   New token expires in: {token_data.get('expires_in', 'Unknown')} seconds")
                    print(f"   New access token: {token_data['access_token'][:20]}...")
                    return True
                else:
                    print(f"❌ No access token in refresh response: {token_data}")
                    return False
            else:
                print(f"❌ Token refresh failed: {response.status_code}")
                print(f"Response: {response.text}")
                return False
                
        except Exception as e:
            print(f"❌ Error refreshing token: {e}")
            return False

    def is_token_expired(self, account):
        """Check if access token is expired or will expire soon"""
        try:
            # If we have expiry info from previous refresh
            if 'token_refreshed_at' in account and 'expires_in' in account:
                refreshed_at = datetime.fromisoformat(account['token_refreshed_at'])
                expires_in_seconds = account['expires_in']
                expires_at = refreshed_at + timedelta(seconds=expires_in_seconds)
                
                # Consider expired if less than 2 hours remaining
                buffer_time = timedelta(hours=2)
                return datetime.now() > (expires_at - buffer_time)
            
            # If no refresh info, assume expired (tokens provided are expired)
            return True
            
        except Exception as e:
            print(f"⚠️ Error checking token expiry: {e}")
            return True  # Assume expired on error

    def get_user_info(self, account):
        """Get current user info (including username) for an account"""
        try:
            print(f"🔍 Fetching user info for account: {account.get('channel_name', 'Unknown')}")
            
            url = f"{self.tiktok_base_url}/v2/user/info/"
            
            headers = {
                'Authorization': f"Bearer {account['access_token']}",
                'Content-Type': 'application/json'
            }
            
            params = {
                'fields': 'open_id,union_id,avatar_url,display_name,username,profile_deep_link,bio_description,is_verified'
            }
            
            response = requests.get(url, headers=headers, params=params)
            print(f"📡 User info API response status: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                
                if data.get('data') and data['data'].get('user'):
                    user_info = data['data']['user']
                    
                    # Debug: Print all available fields
                    print(f"🔍 Available user fields: {list(user_info.keys())}")
                    print(f"🔍 Raw user data: {user_info}")
                    
                    # Try to get username in order of preference
                    username = None
                    
                    # 1. Try the 'username' field first (this should be the handle)
                    if 'username' in user_info and user_info['username']:
                        username = user_info['username']
                        print(f"✅ Got username from 'username' field: {username}")
                    
                    # 2. Try extracting from profile_deep_link
                    elif 'profile_deep_link' in user_info and user_info['profile_deep_link']:
                        profile_link = user_info['profile_deep_link']
                        username_match = re.search(r'tiktok\.com/@([^/?&]+)', profile_link)
                        if username_match:
                            username = username_match.group(1)
                            print(f"✅ Got username from profile_deep_link: {username}")
                    
                    # 3. Fallback to display_name (but clean it up)
                    else:
                        display_name = user_info.get('display_name', '')
                        # Try to convert display name to a handle-like format
                        username = display_name.lower().replace(' ', '.').replace('@', '')
                        print(f"⚠️ Using display_name as username (converted): {username}")
                    
                    print(f"✅ User info retrieved:")
                    print(f"   Final Username: @{username}")
                    print(f"   Display Name: {user_info.get('display_name', 'N/A')}")
                    print(f"   Profile Link: {user_info.get('profile_deep_link', 'N/A')}")
                    
                    return {
                        'username': username,
                        'display_name': user_info.get('display_name', ''),
                        'is_verified': user_info.get('is_verified', False)
                    }
                else:
                    print(f"⚠️ No user data in response: {data}")
                    return None
            else:
                print(f"❌ User info request failed: {response.status_code}")
                print(f"Response: {response.text}")
                return None
                
        except Exception as e:
            print(f"❌ Error fetching user info: {e}")
            return None

    def build_username_mapping(self):
        """Build runtime mapping of username -> account for all TikTok accounts"""
        print(f"\n🔄 Building TikTok username mapping...")
        
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
            
            # Get current user info
            user_info = self.get_user_info(account)
            if user_info and user_info['username']:
                username = user_info['username']
                
                # Store account data with user info
                account_data = account.copy()
                account_data.update(user_info)
                
                self.username_to_account[username] = account_data
                print(f"   ✅ Mapped @{username} → {channel_name}")
            else:
                print(f"   ⚠️ Could not get username for {channel_name}")
            
            # Rate limiting
            time.sleep(0.15)
        
        print(f"\n📊 Username mapping complete:")
        print(f"   Total accounts: {len(self.tiktok_accounts)}")
        print(f"   Successfully mapped: {len(self.username_to_account)}")
        
        for username, account in self.username_to_account.items():
            print(f"   • @{username} → {account.get('channel_name', 'Unknown')}")

    def get_all_posts_from_airtable(self):
        """Get all posts from Posts table"""
        all_posts = []
        offset = None
        
        while True:
            params = {'pageSize': 100}
            if offset:
                params['offset'] = offset
                
            response = requests.get(self.posts_table_url, 
                                  headers=self.airtable_headers, 
                                  params=params)
            data = response.json()
            
            if 'records' not in data:
                print(f"⚠️ Error fetching posts: {data}")
                break
            
            all_posts.extend(data['records'])
            
            offset = data.get('offset')
            if not offset:
                break
        
        return all_posts

    def extract_tiktok_video_id_from_url(self, url):
        """Extract TikTok video ID from URL with short URL resolution"""
        if not url:
            return None, None
        
        original_url = url
        
        # Check if it's a short URL that needs resolution
        if 'vm.tiktok.com' in url or 'tiktok.com/t/' in url:
            print(f"   🔗 Detected short URL, attempting resolution...")
            resolved_url = self.resolve_short_url(url)
            if resolved_url:
                url = resolved_url
                print(f"   ✅ Using resolved URL: {url}")
            else:
                print(f"   ❌ Failed to resolve short URL: {original_url}")
                return None, None
        
        # TikTok URL patterns (now works with resolved URLs too)
        patterns = [
            r'tiktok\.com/@([^/]+)/video/(\d+)',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                # Format: @username/video/123456
                username = match.group(1)
                video_id = match.group(2)
                return username, video_id
        
        print(f"   ⚠️ Could not extract video ID from URL: {url}")
        return None, None

    def get_video_metrics(self, account, video_id):
        """Get TikTok video metrics and creation date"""
        try:
            print(f"      🔍 Fetching TikTok metrics for video: {video_id}")
            
            url = f"{self.tiktok_base_url}/v2/video/query/"
            
            headers = {
                'Authorization': f"Bearer {account['access_token']}",
                'Content-Type': 'application/json'
            }
            
            data = {
                'filters': {
                    'video_ids': [video_id]
                }
            }
            
            # Enhanced params to include create_time for Date Posted
            params = {
                'fields': 'id,title,video_description,duration,cover_image_url,embed_link,view_count,like_count,comment_count,share_count,create_time'
            }
            
            response = requests.post(url, headers=headers, json=data, params=params)
            print(f"      📡 TikTok video API response status: {response.status_code}")
            
            if response.status_code == 200:
                response_data = response.json()
                
                if response_data.get('data') and response_data['data'].get('videos') and len(response_data['data']['videos']) > 0:
                    video_data = response_data['data']['videos'][0]
                    
                    # Parse creation date
                    create_time = video_data.get('create_time')
                    date_posted = None
                    if create_time:
                        try:
                            # TikTok returns Unix timestamp
                            dt = datetime.fromtimestamp(create_time)
                            date_posted = dt.strftime('%Y-%m-%d')
                        except Exception as e:
                            print(f"      ⚠️ Error formatting date {create_time}: {e}")
                    
                    metrics = {
                        'views': video_data.get('view_count', 0),
                        'likes': video_data.get('like_count', 0),
                        'comments': video_data.get('comment_count', 0),
                        'shares': video_data.get('share_count', 0),
                        'date_posted': date_posted
                    }
                    
                    print(f"      ✅ TikTok metrics retrieved: Views={metrics['views']}, Likes={metrics['likes']}, Date={metrics['date_posted']}")
                    return metrics
                else:
                    print(f"      ⚠️ No video data in response: {response_data}")
                    return None
            else:
                print(f"      ❌ Video metrics request failed: {response.status_code}")
                print(f"      Response: {response.text}")
                return None
                
        except Exception as e:
            print(f"      ❌ Error fetching TikTok metrics for {video_id}: {e}")
            return None

    def update_post_record(self, record_id, metrics, existing_date=None):
        """Update TikTok post record with Views, Likes, and Date Posted (if not already set)"""
        try:
            print(f"      💾 Updating Airtable record: {record_id}")
            
            # Build update fields
            update_fields = {
                'Views': metrics['views'],
                'Likes': metrics['likes']
            }
            
            # Handle Date Posted logic
            date_action = "none"
            api_date = metrics.get('date_posted')
            
            if existing_date:
                # Keep existing manual date
                date_action = "preserved"
                print(f"      📊 TikTok metrics: Views={metrics['views']}, Likes={metrics['likes']}, Date=kept existing ({existing_date})")
            elif api_date:
                # Add new date from API
                update_fields['Date Posted'] = api_date
                date_action = "added"
                print(f"      📊 TikTok metrics: Views={metrics['views']}, Likes={metrics['likes']}, Date=added ({api_date})")
            else:
                # No date available from API
                date_action = "unavailable"
                print(f"      📊 TikTok metrics: Views={metrics['views']}, Likes={metrics['likes']}, Date=not available from API")
            
            record = {'fields': update_fields}
            
            url = f"{self.posts_table_url}/{record_id}"
            response = requests.patch(url, 
                                    headers=self.airtable_headers, 
                                    json=record)
            
            print(f"      📡 Airtable update response status: {response.status_code}")
            
            if response.status_code == 200:
                print(f"      ✅ Successfully updated Airtable record")
                return True, date_action
            else:
                error_data = response.json()
                print(f"      ❌ Airtable update failed: {error_data}")
                return False, "error"
                
        except Exception as e:
            print(f"      ❌ Error updating post record: {e}")
            return False, "error"

    def process_single_tiktok_post(self, post_record):
        """Process a single TikTok post"""
        record_id = post_record['id']
        fields = post_record['fields']
        
        post_name = fields.get('Name', 'Unknown')
        post_url = fields.get('Link to Post', '')
        
        print(f"🔍 Processing: {post_name}")
        print(f"   URL: {post_url}")
        print(f"   Record ID: {record_id}")
        
        # Extract username and video ID from URL (now handles short URLs)
        username, video_id = self.extract_tiktok_video_id_from_url(post_url)
        
        if not video_id:
            print(f"   ⚠️ Could not extract video ID from URL")
            return False
        
        if not username:
            print(f"   ⚠️ Could not extract username from URL")
            return False
        
        print(f"   ✅ Extracted: @{username}, Video ID: {video_id}")
        
        # Find account for this username
        if username not in self.username_to_account:
            print(f"   ⚠️ No account mapping found for @{username}")
            return False
        
        account = self.username_to_account[username]
        print(f"   🔗 Using account: {account.get('channel_name', 'Unknown')}")
        
        # Get video metrics
        metrics = self.get_video_metrics(account, video_id)
        
        if metrics:
            print(f"   📊 Metrics retrieved successfully")
            
            # Check for existing Date Posted
            existing_date = fields.get('Date Posted')
            
            result = self.update_post_record(record_id, metrics, existing_date)
            
            # Handle both return formats
            if isinstance(result, tuple):
                success, date_action = result
            else:
                success, date_action = result, "unknown"
            
            if success:
                # Customize success message based on date action
                if date_action == "preserved":
                    print(f"   ✅ Updated: {metrics['views']} views, {metrics['likes']} likes (kept existing date)")
                elif date_action == "added":
                    print(f"   ✅ Updated: {metrics['views']} views, {metrics['likes']} likes, added date: {metrics.get('date_posted')}")
                else:
                    print(f"   ✅ Updated: {metrics['views']} views, {metrics['likes']} likes")
                return True
            else:
                print(f"   ❌ Failed to update Airtable record")
                return False
        else:
            print(f"   ⚠️ Could not fetch metrics")
            return False

    def sync_tiktok_posts(self):
        """Main function to sync TikTok post metrics"""
        print("🎵 Starting TikTok Posts Sync...")
        
        # Step 1: Build username mapping
        if not self.tiktok_accounts:
            print("❌ No TikTok accounts configured")
            return {'success': 0, 'skip': 0, 'error': 0}
        
        self.build_username_mapping()
        
        if not self.username_to_account:
            print("❌ No valid TikTok accounts available")
            return {'success': 0, 'skip': 0, 'error': 0}
        
        # Step 2: Get TikTok posts from Airtable
        print("\n📋 Fetching TikTok posts from Airtable...")
        all_posts = self.get_all_posts_from_airtable()
        print(f"📊 Found {len(all_posts)} total posts")
        
        # Filter for TikTok posts and analyze existing dates
        tiktok_posts = []
        short_url_count = 0
        for post in all_posts:
            social_network = post['fields'].get('Social Network', '')
            post_url = post['fields'].get('Link to Post', '')
            
            if social_network == 'TikTok' and post_url:
                tiktok_posts.append(post)
                # Count short URLs
                if 'vm.tiktok.com' in post_url or 'tiktok.com/t/' in post_url:
                    short_url_count += 1
        
        print(f"🎯 Processing {len(tiktok_posts)} TikTok posts")
        print(f"🔗 Found {short_url_count} short URLs that will be resolved")
        
        if not tiktok_posts:
            print("ℹ️ No TikTok posts found")
            return {'success': 0, 'skip': 0, 'error': 0}
        
        # Analyze existing dates
        posts_with_dates = sum(1 for post in tiktok_posts if post['fields'].get('Date Posted'))
        posts_without_dates = len(tiktok_posts) - posts_with_dates
        
        print(f"📅 Date Analysis:")
        print(f"   Posts with existing dates: {posts_with_dates} (will be preserved)")
        print(f"   Posts without dates: {posts_without_dates} (will get API dates)")
        
        # Step 3: Process each post
        success_count = 0
        skip_count = 0
        error_count = 0
        resolved_short_urls = 0
        
        for i, post in enumerate(tiktok_posts, 1):
            print(f"\n🎵 [{i}/{len(tiktok_posts)}]", end=" ")
            
            # Check if this post has a short URL
            post_url = post['fields'].get('Link to Post', '')
            is_short_url = 'vm.tiktok.com' in post_url or 'tiktok.com/t/' in post_url
            
            try:
                success = self.process_single_tiktok_post(post)
                if success:
                    success_count += 1
                    if is_short_url:
                        resolved_short_urls += 1
                else:
                    skip_count += 1
            except Exception as e:
                print(f"   ❌ Unexpected error: {e}")
                error_count += 1
            
            # Rate limiting
            time.sleep(0.15)
        
        # Enhanced summary
        print(f"\n🎉 TikTok sync completed!")
        print(f"✅ Successfully updated: {success_count}")
        print(f"⏭️ Skipped (no mapping/issues): {skip_count}")
        print(f"❌ Unexpected errors: {error_count}")
        print(f"📊 Total processed: {success_count + skip_count + error_count}")
        
        print(f"\n🔗 Short URL Resolution Summary:")
        print(f"   Short URLs found: {short_url_count}")
        print(f"   Successfully resolved: {resolved_short_urls}")
        print(f"   Resolution success rate: {(resolved_short_urls/short_url_count*100):.1f}%" if short_url_count > 0 else "   No short URLs to resolve")
        
        print(f"\n📅 Date Posted Summary:")
        print(f"   📌 Manual dates preserved and API dates added automatically")
        print(f"   🎵 TikTok creation dates now sync alongside views and likes")
        print(f"   💡 Manual date entry eliminated for posts without existing dates")
        
        if posts_with_dates > 0:
            print(f"   🔒 Your existing manual dates were kept intact - no overwrites!")
        
        return {
            'success': success_count,
            'skip': skip_count,
            'error': error_count,
            'short_urls_resolved': resolved_short_urls
        }

def main():
    sync = TikTokSync()
    
    # Run TikTok posts sync
    sync.sync_tiktok_posts()

if __name__ == "__main__":
    main()