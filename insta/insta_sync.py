# import os
# import json
# import requests
# import re
# from datetime import datetime
# from dotenv import load_dotenv
# import time

# # Load environment variables
# load_dotenv()

# class InstagramDynamicSync:
#     def __init__(self):
#         self.user_access_token = os.getenv('META_USER_ACCESS_TOKEN')
#         self.app_id = os.getenv('META_APP_ID')
#         self.app_secret = os.getenv('META_APP_SECRET')
#         self.airtable_token = os.getenv('AIRTABLE_PERSONAL_ACCESS_TOKEN')
#         self.airtable_base_id = os.getenv('AIRTABLE_BASE_ID')
#         self.posts_table_id = os.getenv('AIRTABLE_TABLE_ID')
#         self.channels_table_id = os.getenv('AIRTABLE_CHANNELS_TABLE_ID', "tblP8bh3crTHLVoZA")
        
#         # API URLs
#         self.meta_base_url = "https://graph.facebook.com/v23.0"
#         self.posts_table_url = f"https://api.airtable.com/v0/{self.airtable_base_id}/{self.posts_table_id}"
#         self.channels_table_url = f"https://api.airtable.com/v0/{self.airtable_base_id}/{self.channels_table_id}"
        
#         # Headers
#         self.airtable_headers = {
#             'Authorization': f'Bearer {self.airtable_token}',
#             'Content-Type': 'application/json'
#         }
        
#         # Runtime mappings built during sync
#         self.username_to_page_data = {}  # instagram_username -> page_data
#         self.discovered_accounts = []

#     def check_token_validity(self):
#         """Check if user access token is still valid"""
#         try:
#             url = f"{self.meta_base_url}/me"
#             params = {'access_token': self.user_access_token}
            
#             response = requests.get(url, params=params)
            
#             if response.status_code == 200:
#                 data = response.json()
#                 print(f"Token valid - User: {data.get('name', 'Unknown')}")
#                 return True
#             else:
#                 print(f"Token validation failed: {response.status_code}")
#                 print(f"Response: {response.text}")
#                 return False
                
#         except Exception as e:
#             print(f"Error checking token validity: {e}")
#             return False

#     def discover_all_pages(self):
#         """Discover all Facebook pages user has access to"""
#         try:
#             print("Discovering Facebook pages...")
            
#             all_pages = []
#             url = f"{self.meta_base_url}/me/accounts"
#             params = {
#                 'access_token': self.user_access_token,
#                 'fields': 'id,name,access_token,category',
#                 'limit': 100
#             }
            
#             while url:
#                 response = requests.get(url, params=params)
                
#                 if response.status_code != 200:
#                     print(f"Error fetching pages: {response.status_code}")
#                     print(f"Response: {response.text}")
#                     break
                
#                 data = response.json()
                
#                 if 'data' in data:
#                     all_pages.extend(data['data'])
#                     print(f"Found {len(data['data'])} pages in this batch")
                    
#                     # Handle pagination
#                     url = data.get('paging', {}).get('next')
#                     params = None  # Next URL already includes params
#                 else:
#                     break
                
#                 time.sleep(0.15)  # Rate limiting
            
#             print(f"Total pages discovered: {len(all_pages)}")
#             return all_pages
            
#         except Exception as e:
#             print(f"Error discovering pages: {e}")
#             return []

#     def get_instagram_account_for_page(self, page_id, page_access_token):
#         """Get Instagram Business account connected to a Facebook page"""
#         try:
#             url = f"{self.meta_base_url}/{page_id}"
#             params = {
#                 'fields': 'instagram_business_account',
#                 'access_token': page_access_token
#             }
            
#             response = requests.get(url, params=params)
            
#             if response.status_code == 200:
#                 data = response.json()
#                 ig_account = data.get('instagram_business_account')
                
#                 if ig_account:
#                     return ig_account['id']
#                 else:
#                     return None
#             else:
#                 print(f"Error getting Instagram account for page {page_id}: {response.status_code}")
#                 return None
                
#         except Exception as e:
#             print(f"Error fetching Instagram account for page {page_id}: {e}")
#             return None

#     def get_instagram_username(self, ig_account_id, page_access_token):
#         """Get Instagram username for an Instagram Business account"""
#         try:
#             url = f"{self.meta_base_url}/{ig_account_id}"
#             params = {
#                 'fields': 'username,followers_count',
#                 'access_token': page_access_token
#             }
            
#             response = requests.get(url, params=params)
            
#             if response.status_code == 200:
#                 data = response.json()
#                 return {
#                     'username': data.get('username'),
#                     'followers_count': data.get('followers_count', 0)
#                 }
#             else:
#                 print(f"Error getting Instagram username for {ig_account_id}: {response.status_code}")
#                 return None
                
#         except Exception as e:
#             print(f"Error fetching Instagram username for {ig_account_id}: {e}")
#             return None

#     def build_instagram_mapping(self):
#         """Build runtime mapping of Instagram usernames to page data"""
#         print("Building Instagram account mapping...")
        
#         if not self.check_token_validity():
#             print("Invalid or expired user access token")
#             return False
        
#         # Discover all pages
#         pages = self.discover_all_pages()
        
#         if not pages:
#             print("No pages found")
#             return False
        
#         # For each page, check for connected Instagram account
#         for page in pages:
#             page_id = page['id']
#             page_name = page['name']
#             page_access_token = page['access_token']
            
#             print(f"\nProcessing page: {page_name} (ID: {page_id})")
            
#             # Get connected Instagram account
#             ig_account_id = self.get_instagram_account_for_page(page_id, page_access_token)
            
#             if ig_account_id:
#                 print(f"   Found connected Instagram account: {ig_account_id}")
                
#                 # Get Instagram username
#                 ig_info = self.get_instagram_username(ig_account_id, page_access_token)
                
#                 if ig_info and ig_info['username']:
#                     username = ig_info['username']
#                     followers = ig_info['followers_count']
                    
#                     print(f"   Instagram: @{username} ({followers:,} followers)")
                    
#                     # Store in mapping
#                     self.username_to_page_data[username] = {
#                         'page_id': page_id,
#                         'page_name': page_name,
#                         'page_access_token': page_access_token,
#                         'ig_account_id': ig_account_id,
#                         'followers_count': followers
#                     }
                    
#                     self.discovered_accounts.append({
#                         'page_name': page_name,
#                         'instagram_username': username,
#                         'followers_count': followers
#                     })
#                 else:
#                     print(f"   Could not get Instagram username")
#             else:
#                 print(f"   No Instagram account connected")
            
#             time.sleep(0.15)  # Rate limiting
        
#         print(f"\nMapping complete:")
#         print(f"   Total pages: {len(pages)}")
#         print(f"   Pages with Instagram: {len(self.username_to_page_data)}")
        
#         for username, data in self.username_to_page_data.items():
#             print(f"   @{username} -> {data['page_name']}")
        
#         return len(self.username_to_page_data) > 0

#     def get_all_posts_from_airtable(self):
#         """Get all posts from Posts table"""
#         all_posts = []
#         offset = None
        
#         while True:
#             params = {'pageSize': 100}
#             if offset:
#                 params['offset'] = offset
                
#             response = requests.get(self.posts_table_url, 
#                                   headers=self.airtable_headers, 
#                                   params=params)
#             data = response.json()
            
#             if 'records' not in data:
#                 print(f"Error fetching posts: {data}")
#                 break
            
#             all_posts.extend(data['records'])
            
#             offset = data.get('offset')
#             if not offset:
#                 break
        
#         return all_posts

#     def get_all_channels_from_airtable(self):
#         """Get all channels from Channels table"""
#         all_channels = []
#         offset = None
        
#         while True:
#             params = {'pageSize': 100}
#             if offset:
#                 params['offset'] = offset
                
#             response = requests.get(self.channels_table_url, 
#                                   headers=self.airtable_headers, 
#                                   params=params)
#             data = response.json()
            
#             if 'records' not in data:
#                 print(f"Error fetching channels: {data}")
#                 break
            
#             all_channels.extend(data['records'])
            
#             offset = data.get('offset')
#             if not offset:
#                 break
        
#         return all_channels

#     def extract_instagram_info_from_url(self, url):
#         """Extract Instagram username and shortcode from URL"""
#         if not url:
#             return None, None
        
#         # Instagram URL patterns
#         patterns = [
#             r'instagram\.com/([^/]+)/p/([^/?&]+)',
#             r'instagram\.com/([^/]+)/reel/([^/?&]+)',
#             r'instagram\.com/p/([^/?&]+)',
#             r'instagram\.com/reel/([^/?&]+)'
#         ]
        
#         for pattern in patterns:
#             match = re.search(pattern, url)
#             if match:
#                 if len(match.groups()) == 2:
#                     # Format: username/p/shortcode
#                     username = match.group(1)
#                     shortcode = match.group(2)
#                     return username, shortcode
#                 else:
#                     # Format: p/shortcode (username unknown)
#                     shortcode = match.group(1)
#                     return None, shortcode
        
#         return None, None

#     def get_media_id_from_shortcode(self, ig_account_id, shortcode, page_access_token):
#         """Get Instagram media ID from shortcode"""
#         try:
#             url = f"{self.meta_base_url}/{ig_account_id}/media"
#             params = {
#                 'access_token': page_access_token,
#                 'fields': 'id,permalink'
#             }
            
#             page_count = 0
#             while url and page_count < 10:  # Limit search
#                 page_count += 1
                
#                 response = requests.get(url, params=params)
                
#                 if response.status_code != 200:
#                     print(f"      Error searching media: {response.status_code}")
#                     break
                
#                 data = response.json()
                
#                 if 'data' not in data:
#                     break
                
#                 for media in data['data']:
#                     permalink = media.get('permalink', '')
#                     media_id = media.get('id', '')
                    
#                     if f'/p/{shortcode}' in permalink or f'/reel/{shortcode}' in permalink:
#                         return media_id
                
#                 # Get next page
#                 url = data.get('paging', {}).get('next')
#                 params = None
#                 time.sleep(0.15)
            
#             return None
            
#         except Exception as e:
#             print(f"      Error finding media ID for {shortcode}: {e}")
#             return None

#     def get_instagram_media_metrics(self, media_id, page_access_token):
#         """Get Instagram media metrics"""
#         try:
#             url = f"{self.meta_base_url}/{media_id}/insights"
#             params = {
#                 'metric': 'likes,comments,reach,shares,saved,total_interactions,views',
#                 'access_token': page_access_token
#             }
            
#             response = requests.get(url, params=params)
            
#             if response.status_code == 200:
#                 data = response.json()
                
#                 if 'data' in data:
#                     metrics = {}
#                     for insight in data['data']:
#                         metric_name = insight['name']
#                         metric_value = insight['values'][0]['value'] if insight['values'] else 0
#                         metrics[metric_name] = metric_value
                    
#                     result = {
#                         'views': metrics.get('views', 0),
#                         'reach': metrics.get('reach', 0),
#                         'likes': metrics.get('likes', 0)
#                     }
#                     return result
#                 else:
#                     print(f"      No insights data available")
#                     return None
#             else:
#                 print(f"      Error fetching insights: {response.status_code}")
#                 return None
                
#         except Exception as e:
#             print(f"      Error fetching Instagram metrics: {e}")
#             return None

#     def update_post_record(self, record_id, metrics):
#         """Update Instagram post record with Views, Reach, and Likes"""
#         try:
#             record = {
#                 'fields': {
#                     'Views': metrics['views'],
#                     'Reach': metrics['reach'],
#                     'Likes': metrics['likes']
#                 }
#             }
            
#             url = f"{self.posts_table_url}/{record_id}"
#             response = requests.patch(url, 
#                                     headers=self.airtable_headers, 
#                                     json=record)
            
#             if response.status_code == 200:
#                 return True
#             else:
#                 print(f"      Airtable update failed: {response.json()}")
#                 return False
                
#         except Exception as e:
#             print(f"      Error updating post record: {e}")
#             return False

#     def update_channel_follower_count(self, channel_record_id, followers_count):
#         """Update channel record with Instagram followers count"""
#         try:
#             record = {
#                 'fields': {
#                     'IG Followers': followers_count
#                 }
#             }
            
#             url = f"{self.channels_table_url}/{channel_record_id}"
#             response = requests.patch(url, 
#                                     headers=self.airtable_headers, 
#                                     json=record)
            
#             return response.status_code == 200
                
#         except Exception as e:
#             print(f"      Error updating channel followers: {e}")
#             return False

#     def get_channel_record(self, channel_id):
#         """Get channel record from Channels table with caching"""
#         url = f"{self.channels_table_url}/{channel_id}"
#         response = requests.get(url, headers=self.airtable_headers)
        
#         if response.status_code == 200:
#             channel_record = response.json()
#             return channel_record['fields']
#         else:
#             print(f"      Error fetching channel {channel_id}: {response.json()}")
#             return None

#     def get_username_from_channel(self, post_fields):
#         """Get Instagram username from post's channel relationship"""
#         try:
#             # Get channel ID from post
#             channel_ids = post_fields.get('Channel', [])
#             if not channel_ids:
#                 print("      No channel found for post")
#                 return None
            
#             # Get channel record
#             channel_record = self.get_channel_record(channel_ids[0])
#             if not channel_record:
#                 print("      Could not fetch channel record")
#                 return None
            
#             # Get Instagram profile URL
#             ig_profile = channel_record.get('IG Profile', '')
#             if not ig_profile:
#                 print("      No Instagram profile found for channel")
#                 return None
            
#             # Extract username from IG profile URL
#             username_match = re.search(r'instagram\.com/([^/?&]+)', ig_profile)
#             if username_match:
#                 username = username_match.group(1).rstrip('`').strip()  # Clean backticks and whitespace
#                 print(f"      Found username from channel: @{username}")
#                 return username
#             else:
#                 print(f"      Could not extract username from IG profile: {ig_profile}")
#                 return None
                
#         except Exception as e:
#             print(f"      Error getting username from channel: {e}")
#             return None

#     def process_instagram_post(self, post_record):
#         """Process a single Instagram post"""
#         record_id = post_record['id']
#         fields = post_record['fields']
        
#         post_name = fields.get('Name', 'Unknown')
#         post_url = fields.get('Link to Post', '')
        
#         # Handle formula errors in Name field
#         if isinstance(post_name, dict) and 'error' in post_name:
#             post_name = f"[Formula Error: {post_name.get('error', 'Unknown')}]"
        
#         print(f"Processing: {str(post_name)[:60]}...")
#         print(f"   URL: {post_url}")
        
#         # Extract username and shortcode from URL
#         url_username, shortcode = self.extract_instagram_info_from_url(post_url)
        
#         if not shortcode:
#             print(f"   Could not extract shortcode from URL")
#             return False
        
#         # Determine username - prefer from URL, fallback to channel relationship
#         username = None
        
#         if url_username:
#             print(f"   Username from URL: @{url_username}")
#             username = url_username
#         else:
#             print(f"   Username not in URL, checking channel relationship...")
#             username = self.get_username_from_channel(fields)
        
#         # Final fallback - search across all accounts (inefficient but works)
#         if not username:
#             print(f"   No username from channel, searching across all accounts...")
#             for search_username, page_data in self.username_to_page_data.items():
#                 media_id = self.get_media_id_from_shortcode(
#                     page_data['ig_account_id'], 
#                     shortcode, 
#                     page_data['page_access_token']
#                 )
#                 if media_id:
#                     username = search_username
#                     print(f"   Found shortcode in @{username} account")
#                     break
        
#         if not username or username not in self.username_to_page_data:
#             print(f"   No account mapping found for username: {username}")
#             return False
        
#         page_data = self.username_to_page_data[username]
#         print(f"   Using account: @{username} ({page_data['page_name']})")
        
#         # Get media ID (only search this specific account now)
#         media_id = self.get_media_id_from_shortcode(
#             page_data['ig_account_id'], 
#             shortcode, 
#             page_data['page_access_token']
#         )
        
#         if not media_id:
#             print(f"   Could not find media for shortcode: {shortcode}")
#             return False
        
#         # Get metrics
#         metrics = self.get_instagram_media_metrics(media_id, page_data['page_access_token'])
        
#         if metrics:
#             success = self.update_post_record(record_id, metrics)
#             if success:
#                 print(f"   Updated: {metrics['views']} views, {metrics['reach']} reach, {metrics['likes']} likes")
#                 return True
#             else:
#                 print(f"   Failed to update Airtable record")
#                 return False
#         else:
#             print(f"   Could not fetch metrics")
#             return False

#     def sync_instagram_posts(self):
#         """Main function to sync Instagram post metrics"""
#         print("Starting Instagram Posts Sync...")
        
#         # Build mapping
#         if not self.build_instagram_mapping():
#             print("Failed to build Instagram mapping")
#             return
        
#         # Get Instagram posts from Airtable
#         print("\nFetching Instagram posts from Airtable...")
#         all_posts = self.get_all_posts_from_airtable()
#         print(f"Found {len(all_posts)} total posts")
        
#         # Filter for Instagram posts
#         instagram_posts = []
#         for post in all_posts:
#             social_network = post['fields'].get('Social Network', '')
#             post_url = post['fields'].get('Link to Post', '')
            
#             if social_network == 'Instagram' and post_url:
#                 instagram_posts.append(post)
        
#         print(f"Processing {len(instagram_posts)} Instagram posts")
        
#         if not instagram_posts:
#             print("No Instagram posts found")
#             return
        
#         # Process each post
#         success_count = 0
#         skip_count = 0
#         error_count = 0
        
#         for i, post in enumerate(instagram_posts, 1):
#             print(f"\n[{i}/{len(instagram_posts)}]", end=" ")
            
#             try:
#                 success = self.process_instagram_post(post)
#                 if success:
#                     success_count += 1
#                 else:
#                     skip_count += 1
#             except Exception as e:
#                 print(f"   Unexpected error: {e}")
#                 error_count += 1
            
#             time.sleep(0.15)
        
#         # Summary
#         print(f"\nInstagram sync completed!")
#         print(f"Successfully updated: {success_count}")
#         print(f"Skipped (no mapping/issues): {skip_count}")
#         print(f"Unexpected errors: {error_count}")
#         print(f"Total processed: {success_count + skip_count + error_count}")

#     def sync_instagram_followers(self):
#         """Sync Instagram follower counts to Channels table"""
#         print("Starting Instagram Followers Sync...")
        
#         # Build mapping (includes follower counts)
#         if not self.build_instagram_mapping():
#             print("Failed to build Instagram mapping")
#             return
        
#         # Get channels from Airtable
#         print("\nFetching channels from Airtable...")
#         all_channels = self.get_all_channels_from_airtable()
#         print(f"Found {len(all_channels)} total channels")
        
#         # Match channels with discovered Instagram accounts
#         success_count = 0
#         skip_count = 0
        
#         for channel in all_channels:
#             fields = channel['fields']
#             channel_name = fields.get('Social Media Account', 'Unknown')
#             ig_profile = fields.get('IG Profile', '')
            
#             if ig_profile:
#                 # Extract username from IG profile URL
#                 username_match = re.search(r'instagram\.com/([^/?&]+)', ig_profile)
#                 if username_match:
#                     username = username_match.group(1)
                    
#                     if username in self.username_to_page_data:
#                         page_data = self.username_to_page_data[username]
#                         followers_count = page_data['followers_count']
                        
#                         print(f"Updating {channel_name} (@{username}): {followers_count:,} followers")
                        
#                         success = self.update_channel_follower_count(
#                             channel['id'], 
#                             followers_count
#                         )
                        
#                         if success:
#                             success_count += 1
#                         else:
#                             skip_count += 1
#                     else:
#                         print(f"No Instagram account found for {channel_name} (@{username})")
#                         skip_count += 1
#                 else:
#                     print(f"Could not extract username from IG profile: {ig_profile}")
#                     skip_count += 1
#             else:
#                 skip_count += 1
        
#         print(f"\nInstagram followers sync completed!")
#         print(f"Successfully updated: {success_count}")
#         print(f"Skipped: {skip_count}")

# def main():
#     sync = InstagramDynamicSync()
    
#     # Sync both posts and followers
#     sync.sync_instagram_posts()
#     print("\n" + "="*60 + "\n")
#     sync.sync_instagram_followers()

# if __name__ == "__main__":
#     main()



import os
import json
import requests
import re
from datetime import datetime
from dotenv import load_dotenv
import time

# Load environment variables
load_dotenv()

class InstagramDynamicSync:
    def __init__(self):
        self.user_access_token = os.getenv('META_USER_ACCESS_TOKEN')
        self.app_id = os.getenv('META_APP_ID')
        self.app_secret = os.getenv('META_APP_SECRET')
        self.airtable_token = os.getenv('AIRTABLE_PERSONAL_ACCESS_TOKEN')
        self.airtable_base_id = os.getenv('AIRTABLE_BASE_ID')
        self.posts_table_id = os.getenv('AIRTABLE_TABLE_ID')
        self.channels_table_id = os.getenv('AIRTABLE_CHANNELS_TABLE_ID', "tblP8bh3crTHLVoZA")
        
        # API URLs
        self.meta_base_url = "https://graph.facebook.com/v23.0"
        self.posts_table_url = f"https://api.airtable.com/v0/{self.airtable_base_id}/{self.posts_table_id}"
        self.channels_table_url = f"https://api.airtable.com/v0/{self.airtable_base_id}/{self.channels_table_id}"
        
        # Headers
        self.airtable_headers = {
            'Authorization': f'Bearer {self.airtable_token}',
            'Content-Type': 'application/json'
        }
        
        # Runtime mappings built during sync
        self.username_to_page_data = {}  # instagram_username -> page_data
        self.discovered_accounts = []
        self.channel_cache = {}  # channel_id -> fields, avoids repeated Airtable GETs

        # How many days back to re-sync posts that already have metrics.
        # Posts outside this window with existing metrics are skipped (they rarely change).
        # Set INSTAGRAM_LOOKBACK_DAYS=0 to only sync posts with no Views at all.
        # Set INSTAGRAM_LOOKBACK_DAYS=-1 to sync everything (original behaviour, will likely timeout).
        self.lookback_days = int(os.getenv('INSTAGRAM_LOOKBACK_DAYS', '90'))

    def check_token_validity(self):
        """Check if user access token is still valid"""
        try:
            url = f"{self.meta_base_url}/me"
            params = {'access_token': self.user_access_token}
            
            response = requests.get(url, params=params)
            
            if response.status_code == 200:
                data = response.json()
                print(f"Token valid - User: {data.get('name', 'Unknown')}")
                return True
            else:
                print(f"Token validation failed: {response.status_code}")
                print(f"Response: {response.text}")
                return False
                
        except Exception as e:
            print(f"Error checking token validity: {e}")
            return False

    def discover_all_pages(self):
        """Discover all Facebook pages user has access to"""
        try:
            print("Discovering Facebook pages...")
            
            all_pages = []
            url = f"{self.meta_base_url}/me/accounts"
            params = {
                'access_token': self.user_access_token,
                'fields': 'id,name,access_token,category',
                'limit': 100
            }
            
            while url:
                response = requests.get(url, params=params)
                
                if response.status_code != 200:
                    print(f"Error fetching pages: {response.status_code}")
                    print(f"Response: {response.text}")
                    break
                
                data = response.json()
                
                if 'data' in data:
                    all_pages.extend(data['data'])
                    print(f"Found {len(data['data'])} pages in this batch")
                    
                    # Handle pagination
                    url = data.get('paging', {}).get('next')
                    params = None  # Next URL already includes params
                else:
                    break
                
                time.sleep(0.10)  # Rate limiting
            
            print(f"Total pages discovered: {len(all_pages)}")
            return all_pages
            
        except Exception as e:
            print(f"Error discovering pages: {e}")
            return []

    def get_instagram_account_for_page(self, page_id, page_access_token):
        """Get Instagram Business account connected to a Facebook page"""
        try:
            url = f"{self.meta_base_url}/{page_id}"
            params = {
                'fields': 'instagram_business_account',
                'access_token': page_access_token
            }
            
            response = requests.get(url, params=params)
            
            if response.status_code == 200:
                data = response.json()
                ig_account = data.get('instagram_business_account')
                
                if ig_account:
                    return ig_account['id']
                else:
                    return None
            else:
                print(f"Error getting Instagram account for page {page_id}: {response.status_code}")
                return None
                
        except Exception as e:
            print(f"Error fetching Instagram account for page {page_id}: {e}")
            return None

    def get_instagram_username(self, ig_account_id, page_access_token):
        """Get Instagram username for an Instagram Business account"""
        try:
            url = f"{self.meta_base_url}/{ig_account_id}"
            params = {
                'fields': 'username,followers_count',
                'access_token': page_access_token
            }
            
            response = requests.get(url, params=params)
            
            if response.status_code == 200:
                data = response.json()
                return {
                    'username': data.get('username'),
                    'followers_count': data.get('followers_count', 0)
                }
            else:
                print(f"Error getting Instagram username for {ig_account_id}: {response.status_code}")
                return None
                
        except Exception as e:
            print(f"Error fetching Instagram username for {ig_account_id}: {e}")
            return None

    def build_instagram_mapping(self):
        """Build runtime mapping of Instagram usernames to page data"""
        if self.username_to_page_data:
            print("Instagram mapping already built, reusing...")
            return True

        print("Building Instagram account mapping...")

        if not self.check_token_validity():
            print("Invalid or expired user access token")
            return False
        
        # Discover all pages
        pages = self.discover_all_pages()
        
        if not pages:
            print("No pages found")
            return False
        
        # For each page, check for connected Instagram account
        for page in pages:
            page_id = page['id']
            page_name = page['name']
            page_access_token = page['access_token']
            
            print(f"\nProcessing page: {page_name} (ID: {page_id})")
            
            # Get connected Instagram account
            ig_account_id = self.get_instagram_account_for_page(page_id, page_access_token)
            
            if ig_account_id:
                print(f"   Found connected Instagram account: {ig_account_id}")
                
                # Get Instagram username
                ig_info = self.get_instagram_username(ig_account_id, page_access_token)
                
                if ig_info and ig_info['username']:
                    username = ig_info['username']
                    followers = ig_info['followers_count']
                    
                    print(f"   Instagram: @{username} ({followers:,} followers)")
                    
                    # Store in mapping
                    self.username_to_page_data[username] = {
                        'page_id': page_id,
                        'page_name': page_name,
                        'page_access_token': page_access_token,
                        'ig_account_id': ig_account_id,
                        'followers_count': followers
                    }
                    
                    self.discovered_accounts.append({
                        'page_name': page_name,
                        'instagram_username': username,
                        'followers_count': followers
                    })
                else:
                    print(f"   Could not get Instagram username")
            else:
                print(f"   No Instagram account connected")
            
            time.sleep(0.10)  # Rate limiting
        
        print(f"\nMapping complete:")
        print(f"   Total pages: {len(pages)}")
        print(f"   Pages with Instagram: {len(self.username_to_page_data)}")
        
        for username, data in self.username_to_page_data.items():
            print(f"   @{username} -> {data['page_name']}")
        
        return len(self.username_to_page_data) > 0

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
            if response.status_code != 200:
                print(f"Error fetching posts: {response.status_code} {response.text}")
                break
            data = response.json()

            if 'records' not in data:
                print(f"Error fetching posts: {data}")
                break

            all_posts.extend(data['records'])

            offset = data.get('offset')
            if not offset:
                break
            time.sleep(0.2)

        return all_posts

    def get_instagram_posts_from_airtable(self):
        """Fetch Instagram posts that need syncing.

        Strategy (controlled by INSTAGRAM_LOOKBACK_DAYS env var, default 30):
          - Always include posts with no Views yet (new/unsynced posts).
          - Also include posts whose Date Posted is within the lookback window,
            so that metrics for recent content stay fresh.
          - Skip old posts that already have Views — they rarely change and
            re-processing them every run is what causes cron timeouts.
          - Set INSTAGRAM_LOOKBACK_DAYS=-1 to process everything (no filter).
        Results are sorted newest-first so that fresh posts are always handled
        even if the job times out before reaching older ones.
        """
        all_posts = []
        offset = None

        if self.lookback_days < 0:
            filter_formula = "AND({Social Network}='Instagram', {Link to Post}!='')"
        else:
            filter_formula = (
                "AND("
                "  {Social Network}='Instagram',"
                "  {Link to Post}!='',"
                "  OR("
                "    NOT({Views}),"
                f"    IS_AFTER({{Date Posted}}, DATEADD(TODAY(), -{self.lookback_days}, 'days'))"
                "  )"
                ")"
            )

        params_base = {
            'pageSize': 100,
            'filterByFormula': filter_formula,
            'sort[0][field]': 'Date Posted',
            'sort[0][direction]': 'desc',
        }

        print(f"  Filter: {'all posts' if self.lookback_days < 0 else f'unsynced + last {self.lookback_days} days'}")

        while True:
            params = dict(params_base)
            if offset:
                params['offset'] = offset

            response = requests.get(self.posts_table_url,
                                    headers=self.airtable_headers,
                                    params=params)
            if response.status_code != 200:
                print(f"Error fetching Instagram posts: {response.status_code} {response.text}")
                break
            data = response.json()

            if 'records' not in data:
                print(f"Error fetching Instagram posts: {data}")
                break

            all_posts.extend(data['records'])

            offset = data.get('offset')
            if not offset:
                break
            time.sleep(0.2)

        return all_posts

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
                print(f"Error fetching channels: {data}")
                break
            
            all_channels.extend(data['records'])
            
            offset = data.get('offset')
            if not offset:
                break
        
        return all_channels

    def extract_instagram_info_from_url(self, url):
        """Extract Instagram username and shortcode from URL"""
        if not url:
            return None, None
        
        # Instagram URL patterns
        patterns = [
            r'instagram\.com/([^/]+)/p/([^/?&]+)',
            r'instagram\.com/([^/]+)/reel/([^/?&]+)',
            r'instagram\.com/p/([^/?&]+)',
            r'instagram\.com/reel/([^/?&]+)'
        ]
        
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                if len(match.groups()) == 2:
                    # Format: username/p/shortcode
                    username = match.group(1)
                    shortcode = match.group(2)
                    return username, shortcode
                else:
                    # Format: p/shortcode (username unknown)
                    shortcode = match.group(1)
                    return None, shortcode
        
        return None, None

    def get_media_id_from_shortcode(self, ig_account_id, shortcode, page_access_token):
        """Get Instagram media ID and timestamp from shortcode. Returns (media_id, timestamp) tuple."""
        try:
            url = f"{self.meta_base_url}/{ig_account_id}/media"
            params = {
                'access_token': page_access_token,
                'fields': 'id,shortcode,timestamp',  # fetch shortcode directly; also grab timestamp here
                'limit': 50,
            }

            page_count = 0
            while url and page_count < 10:
                page_count += 1

                response = requests.get(url, params=params)

                if response.status_code != 200:
                    print(f"      Error searching media: {response.status_code}")
                    break

                data = response.json()

                if 'data' not in data:
                    break

                for media in data['data']:
                    if media.get('shortcode') == shortcode:
                        return media['id'], media.get('timestamp')

                url = data.get('paging', {}).get('next')
                params = None
                time.sleep(0.10)

            return None, None

        except Exception as e:
            print(f"      Error finding media ID for {shortcode}: {e}")
            return None, None

    def get_instagram_media_details(self, media_id, page_access_token):
        """Get Instagram media details including metrics and timestamp"""
        try:
            url = f"{self.meta_base_url}/{media_id}"
            params = {
                'fields': 'id,timestamp,caption,permalink',
                'access_token': page_access_token
            }
            
            response = requests.get(url, params=params)
            
            if response.status_code == 200:
                return response.json()
            else:
                print(f"      Error fetching media details: {response.status_code}")
                return None
                
        except Exception as e:
            print(f"      Error fetching media details: {e}")
            return None

    def get_instagram_media_metrics(self, media_id, page_access_token, timestamp=None):
        """Get Instagram media metrics. timestamp should be pre-fetched from get_media_id_from_shortcode."""
        try:
            url = f"{self.meta_base_url}/{media_id}/insights"
            params = {
                'metric': 'likes,comments,reach,shares,saved,total_interactions,views',
                'access_token': page_access_token
            }

            response = requests.get(url, params=params)

            if response.status_code == 200:
                data = response.json()

                if 'data' in data:
                    metrics = {}
                    for insight in data['data']:
                        metric_name = insight['name']
                        metric_value = insight['values'][0]['value'] if insight.get('values') else 0
                        metrics[metric_name] = metric_value

                    result = {
                        'views': metrics.get('views', metrics.get('impressions', 0)),  # photos use impressions
                        'reach': metrics.get('reach', 0),
                        'likes': metrics.get('likes', 0)
                    }

                    if timestamp:
                        result['timestamp'] = timestamp

                    return result
                else:
                    print(f"      No insights data available")
                    return None
            else:
                print(f"      Error fetching insights: {response.status_code}")
                return None

        except Exception as e:
            print(f"      Error fetching Instagram metrics: {e}")
            return None


    def format_instagram_date(self, iso_timestamp):
        """Convert Instagram ISO timestamp to Airtable date format"""
        try:
            # Instagram timestamp format: 2025-08-15T15:00:00+0000
            # Need to convert +0000 to +00:00 for Python parsing
            if '+0000' in iso_timestamp:
                iso_timestamp = iso_timestamp.replace('+0000', '+00:00')
            elif iso_timestamp.endswith('Z'):
                iso_timestamp = iso_timestamp.replace('Z', '+00:00')
            
            # Parse the corrected timestamp
            dt = datetime.fromisoformat(iso_timestamp)
            
            # Convert to YYYY-MM-DD format for Airtable
            return dt.strftime('%Y-%m-%d')
            
        except Exception as e:
            print(f"      Error formatting date: {e}")
            return None
    # def format_instagram_date(self, iso_timestamp):
    #     """Convert Instagram ISO timestamp to Airtable date format"""
    #     try:
    #         # Instagram timestamp format: 2024-12-15T18:30:45+0000
    #         # Parse the timestamp
    #         dt = datetime.fromisoformat(iso_timestamp.replace('Z', '+00:00'))
            
    #         # Convert to YYYY-MM-DD format for Airtable
    #         return dt.strftime('%Y-%m-%d')
            
    #     except Exception as e:
    #         print(f"      Error formatting date: {e}")
    #         return None

    def update_post_record(self, record_id, metrics, current_date_posted=None):
        """Update Instagram post record with Views, Reach, Likes, and Date Posted.
        current_date_posted comes from the already-fetched Airtable record — no extra GET needed."""
        try:
            update_fields = {
                'Views': metrics['views'],
                'Reach': metrics['reach'],
                'Likes': metrics['likes']
            }

            if 'timestamp' in metrics and metrics['timestamp']:
                if current_date_posted:
                    print(f"      Date Posted already exists: {current_date_posted} (preserving)")
                else:
                    formatted_date = self.format_instagram_date(metrics['timestamp'])
                    if formatted_date:
                        update_fields['Date Posted'] = formatted_date
                        print(f"      Adding Date Posted: {formatted_date}")

            url = f"{self.posts_table_url}/{record_id}"
            response = requests.patch(url,
                                    headers=self.airtable_headers,
                                    json={'fields': update_fields})

            if response.status_code == 200:
                return True
            else:
                print(f"      Airtable update failed: {response.json()}")
                return False

        except Exception as e:
            print(f"      Error updating post record: {e}")
            return False

    def update_channel_follower_count(self, channel_record_id, followers_count):
        """Update channel record with Instagram followers count"""
        try:
            record = {
                'fields': {
                    'IG Followers': followers_count
                }
            }
            
            url = f"{self.channels_table_url}/{channel_record_id}"
            response = requests.patch(url, 
                                    headers=self.airtable_headers, 
                                    json=record)
            
            return response.status_code == 200
                
        except Exception as e:
            print(f"      Error updating channel followers: {e}")
            return False

    def get_channel_record(self, channel_id):
        """Get channel record from Channels table, cached for the lifetime of a sync run."""
        if channel_id in self.channel_cache:
            return self.channel_cache[channel_id]

        url = f"{self.channels_table_url}/{channel_id}"
        response = requests.get(url, headers=self.airtable_headers)

        if response.status_code == 200:
            fields = response.json()['fields']
            self.channel_cache[channel_id] = fields
            return fields
        else:
            print(f"      Error fetching channel {channel_id}: {response.json()}")
            return None

    def get_username_from_channel(self, post_fields):
        """Get Instagram username from post's channel relationship"""
        try:
            # Get channel ID from post
            channel_ids = post_fields.get('Social Media Accounts', [])
            if not channel_ids:
                print("      No channel found for post")
                return None
            
            # Get channel record
            channel_record = self.get_channel_record(channel_ids[0])
            if not channel_record:
                print("      Could not fetch channel record")
                return None
            
            # Get Instagram profile URL
            ig_profile = channel_record.get('IG Profile', '')
            if not ig_profile:
                print("      No Instagram profile found for channel")
                return None
            
            # Extract username from IG profile URL
            username_match = re.search(r'instagram\.com/([^/?&]+)', ig_profile)
            if username_match:
                username = username_match.group(1).rstrip('`').strip()  # Clean backticks and whitespace
                print(f"      Found username from channel: @{username}")
                return username
            else:
                print(f"      Could not extract username from IG profile: {ig_profile}")
                return None
                
        except Exception as e:
            print(f"      Error getting username from channel: {e}")
            return None

    def process_instagram_post(self, post_record):
        """Process a single Instagram post"""
        record_id = post_record['id']
        fields = post_record['fields']

        post_name = fields.get('Name', 'Unknown')
        post_url = fields.get('Link to Post', '')
        current_date_posted = fields.get('Date Posted')  # already loaded, no extra GET needed

        if isinstance(post_name, dict) and 'error' in post_name:
            post_name = f"[Formula Error: {post_name.get('error', 'Unknown')}]"

        print(f"Processing: {str(post_name)[:60]}...")
        print(f"   URL: {post_url}")

        url_username, shortcode = self.extract_instagram_info_from_url(post_url)

        if not shortcode:
            print(f"   Could not extract shortcode from URL")
            return False

        username = None
        found_media_id = None
        found_timestamp = None

        if url_username:
            print(f"   Username from URL: @{url_username}")
            username = url_username
        else:
            print(f"   Username not in URL, checking channel relationship...")
            username = self.get_username_from_channel(fields)

        if not username:
            print(f"   No username from channel, searching across all accounts...")
            for search_username, page_data in self.username_to_page_data.items():
                mid, ts = self.get_media_id_from_shortcode(
                    page_data['ig_account_id'],
                    shortcode,
                    page_data['page_access_token']
                )
                if mid:
                    username = search_username
                    found_media_id = mid      # reuse — don't search again below
                    found_timestamp = ts
                    print(f"   Found shortcode in @{username} account")
                    break

        if not username or username not in self.username_to_page_data:
            print(f"   No account mapping found for username: {username}")
            return False

        page_data = self.username_to_page_data[username]
        print(f"   Using account: @{username} ({page_data['page_name']})")

        if not found_media_id:
            found_media_id, found_timestamp = self.get_media_id_from_shortcode(
                page_data['ig_account_id'],
                shortcode,
                page_data['page_access_token']
            )

        if not found_media_id:
            print(f"   Could not find media for shortcode: {shortcode}")
            return False

        metrics = self.get_instagram_media_metrics(
            found_media_id, page_data['page_access_token'], timestamp=found_timestamp
        )

        if metrics:
            success = self.update_post_record(record_id, metrics, current_date_posted=current_date_posted)
            if success:
                print(f"   Updated: {metrics['views']} views, {metrics['reach']} reach, {metrics['likes']} likes")
                return True
            else:
                print(f"   Failed to update Airtable record")
                return False
        else:
            print(f"   Could not fetch metrics")
            return False

    def sync_instagram_posts(self):
        """Main function to sync Instagram post metrics"""
        print("Starting Instagram Posts Sync...")

        if not self.build_instagram_mapping():
            print("Failed to build Instagram mapping")
            return

        print("\nFetching Instagram posts from Airtable...")
        instagram_posts = self.get_instagram_posts_from_airtable()
        print(f"Processing {len(instagram_posts)} Instagram posts")
        
        if not instagram_posts:
            print("No Instagram posts found")
            return
        
        # Process each post
        success_count = 0
        skip_count = 0
        error_count = 0
        
        for i, post in enumerate(instagram_posts, 1):
            print(f"\n[{i}/{len(instagram_posts)}]", end=" ")
            
            try:
                success = self.process_instagram_post(post)
                if success:
                    success_count += 1
                else:
                    skip_count += 1
            except Exception as e:
                print(f"   Unexpected error: {e}")
                error_count += 1
            
            time.sleep(0.10)
        
        # Summary
        print(f"\nInstagram sync completed!")
        print(f"Successfully updated: {success_count}")
        print(f"Skipped (no mapping/issues): {skip_count}")
        print(f"Unexpected errors: {error_count}")
        print(f"Total processed: {success_count + skip_count + error_count}")

    def sync_instagram_followers(self):
        """Sync Instagram follower counts to Channels table"""
        print("Starting Instagram Followers Sync...")
        
        # Build mapping (includes follower counts)
        if not self.build_instagram_mapping():
            print("Failed to build Instagram mapping")
            return
        
        # Get channels from Airtable
        print("\nFetching channels from Airtable...")
        all_channels = self.get_all_channels_from_airtable()
        print(f"Found {len(all_channels)} total channels")
        
        # Match channels with discovered Instagram accounts
        success_count = 0
        skip_count = 0
        
        for channel in all_channels:
            fields = channel['fields']
            channel_name = fields.get('Social Media Account', 'Unknown')
            ig_profile = fields.get('IG Profile', '')
            
            if ig_profile:
                # Extract username from IG profile URL
                username_match = re.search(r'instagram\.com/([^/?&]+)', ig_profile)
                if username_match:
                    username = username_match.group(1)
                    
                    if username in self.username_to_page_data:
                        page_data = self.username_to_page_data[username]
                        followers_count = page_data['followers_count']
                        
                        print(f"Updating {channel_name} (@{username}): {followers_count:,} followers")
                        
                        success = self.update_channel_follower_count(
                            channel['id'], 
                            followers_count
                        )
                        
                        if success:
                            success_count += 1
                        else:
                            skip_count += 1
                    else:
                        print(f"No Instagram account found for {channel_name} (@{username})")
                        skip_count += 1
                else:
                    print(f"Could not extract username from IG profile: {ig_profile}")
                    skip_count += 1
            else:
                skip_count += 1
        
        print(f"\nInstagram followers sync completed!")
        print(f"Successfully updated: {success_count}")
        print(f"Skipped: {skip_count}")

def main():
    sync = InstagramDynamicSync()
    
    # Sync both posts and followers
    sync.sync_instagram_posts()
    print("\n" + "="*60 + "\n")
    sync.sync_instagram_followers()

if __name__ == "__main__":
    main()