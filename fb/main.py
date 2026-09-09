# import os
# import json
# import requests
# import re
# from datetime import datetime
# from dotenv import load_dotenv
# import time

# # Load environment variables
# load_dotenv()

# class FacebookSync:
#     def __init__(self):
#         self.airtable_token = os.getenv('AIRTABLE_PERSONAL_ACCESS_TOKEN')
#         self.airtable_base_id = os.getenv('AIRTABLE_BASE_ID')
#         self.posts_table_id = os.getenv('AIRTABLE_TABLE_ID', "tblMpYJQjbb5yuKfC")
#         self.channels_table_id = os.getenv('AIRTABLE_CHANNELS_TABLE_ID', "tblP8bh3crTHLVoZA")
        
#         # Facebook API URLs
#         self.facebook_base_url = "https://graph.facebook.com/v21.0"
        
#         # Airtable URLs
#         self.posts_table_url = f"https://api.airtable.com/v0/{self.airtable_base_id}/{self.posts_table_id}"
#         self.channels_table_url = f"https://api.airtable.com/v0/{self.airtable_base_id}/{self.channels_table_id}"
        
#         # Headers
#         self.airtable_headers = {
#             'Authorization': f'Bearer {self.airtable_token}',
#             'Content-Type': 'application/json'
#         }
        
#         # Load Facebook pages from environment
#         facebook_pages_json = os.getenv('FACEBOOK_PAGES', '[]')
#         try:
#             self.facebook_pages = json.loads(facebook_pages_json)
#             print(f"✅ Loaded {len(self.facebook_pages)} Facebook pages from environment")
#         except (json.JSONDecodeError, KeyError) as e:
#             print(f"❌ Error loading Facebook pages from environment: {e}")
#             self.facebook_pages = []
        
#         # Build page mappings
#         self.page_id_to_token = {}
#         self.page_name_to_id = {}
#         self.channel_name_to_record = {}
        
#         # Initialize page mappings
#         for page in self.facebook_pages:
#             page_id = page.get('page_id')
#             page_name = page.get('page_name')
#             access_token = page.get('page_access_token')
            
#             if page_id and access_token:
#                 self.page_id_to_token[page_id] = access_token
#             if page_name and page_id:
#                 self.page_name_to_id[page_name] = page_id

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
#                 print(f"⚠️ Error fetching posts: {data}")
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
#                 print(f"⚠️ Error fetching channels: {data}")
#                 break
            
#             all_channels.extend(data['records'])
            
#             offset = data.get('offset')
#             if not offset:
#                 break
        
#         return all_channels

#     def build_channel_mapping(self):
#         """Build mapping of channel names to Airtable record IDs"""
#         print(f"📋 Building channel mapping from Airtable...")
        
#         channels = self.get_all_channels_from_airtable()
#         print(f"📊 Found {len(channels)} channels in Airtable")
        
#         for channel in channels:
#             fields = channel['fields']
#             channel_name = fields.get('Channel', '').strip()
#             facebook_profile = fields.get('Facebook Profile', '').strip()
#             current_followers = fields.get('Facebook Followers', 0)
            
#             if channel_name:
#                 self.channel_name_to_record[channel_name] = {
#                     'record_id': channel['id'],
#                     'facebook_profile': facebook_profile,
#                     'current_followers': current_followers
#                 }
#                 print(f"   • {channel_name} → {current_followers:,} followers")
        
#         print(f"✅ Mapped {len(self.channel_name_to_record)} channels")

#     def extract_facebook_post_info(self, url):
#         """Extract post ID and page info from Facebook URL"""
#         if not url:
#             return None, None
        
#         url = url.strip()
        
#         # Pattern 1: https://www.facebook.com/share/v/19evpwDbkQ/
#         share_v_match = re.search(r'facebook\.com/share/v/([^/?&]+)', url)
#         if share_v_match:
#             post_id = share_v_match.group(1)
#             return post_id, None  # Page ID unknown from this format
        
#         # Pattern 2: https://www.facebook.com/reel/661787706975389
#         reel_match = re.search(r'facebook\.com/reel/([^/?&]+)', url)
#         if reel_match:
#             post_id = reel_match.group(1)
#             return post_id, None  # Page ID unknown from this format
        
#         # Pattern 3: https://www.facebook.com/share/r/1615XFt34f/
#         share_r_match = re.search(r'facebook\.com/share/r/([^/?&]+)', url)
#         if share_r_match:
#             post_id = share_r_match.group(1)
#             return post_id, None  # Page ID unknown from this format
        
#         # Pattern 4: Standard post format (if encountered)
#         standard_match = re.search(r'facebook\.com/([^/]+)/posts/([^/?&]+)', url)
#         if standard_match:
#             page_name = standard_match.group(1)
#             post_id = standard_match.group(2)
#             return post_id, page_name
        
#         print(f"⚠️ Could not extract post ID from URL: {url}")
#         return None, None

#     def resolve_post_page_id(self, post_id):
#         """Try to determine which page owns this post by testing all page tokens"""
#         for page_id, access_token in self.page_id_to_token.items():
#             try:
#                 # Test if this page can access the post
#                 url = f"{self.facebook_base_url}/{post_id}"
#                 params = {'access_token': access_token, 'fields': 'id'}
                
#                 response = requests.get(url, params=params)
#                 if response.status_code == 200:
#                     print(f"      ✅ Post belongs to page: {page_id}")
#                     return page_id
#             except:
#                 continue
        
#         print(f"      ⚠️ Could not determine page for post: {post_id}")
#         return None

#     def get_facebook_post_metrics(self, post_id, page_id=None):
#         """Get Facebook post metrics (likes and video views)"""
#         try:
#             # If page_id not provided, try to resolve it
#             if not page_id:
#                 page_id = self.resolve_post_page_id(post_id)
#                 if not page_id:
#                     return None
            
#             access_token = self.page_id_to_token.get(page_id)
#             if not access_token:
#                 print(f"      ❌ No access token for page: {page_id}")
#                 return None
            
#             print(f"      🔍 Fetching Facebook metrics for post: {post_id}")
            
#             # Get video insights for views and likes
#             url = f"{self.facebook_base_url}/{post_id}/video_insights"
#             params = {
#                 'access_token': access_token,
#                 'metric': 'post_video_likes_by_reaction_type,fb_reels_total_plays'
#             }
            
#             response = requests.get(url, params=params)
#             print(f"      📡 Facebook API response status: {response.status_code}")
            
#             if response.status_code == 200:
#                 data = response.json()
                
#                 views = 0
#                 likes = 0
                
#                 if 'data' in data:
#                     for metric in data['data']:
#                         metric_name = metric.get('name')
#                         values = metric.get('values', [])
                        
#                         if metric_name == 'fb_reels_total_plays' and values:
#                             views = values[0].get('value', 0)
#                         elif metric_name == 'post_video_likes_by_reaction_type' and values:
#                             # Sum all reaction types for total likes
#                             reactions = values[0].get('value', {})
#                             likes = sum(reactions.values()) if reactions else 0
                
#                 metrics = {
#                     'views': views,
#                     'likes': likes
#                 }
                
#                 print(f"      ✅ Facebook metrics retrieved: Views={metrics['views']}, Likes={metrics['likes']}")
#                 return metrics
#             else:
#                 print(f"      ❌ Video insights request failed: {response.status_code}")
#                 print(f"      Response: {response.text}")
#                 return None
                
#         except Exception as e:
#             print(f"      ❌ Error fetching Facebook metrics for {post_id}: {e}")
#             return None

#     def get_facebook_page_followers(self, page_id):
#         """Get Facebook page follower count"""
#         try:
#             access_token = self.page_id_to_token.get(page_id)
#             if not access_token:
#                 print(f"      ❌ No access token for page: {page_id}")
#                 return None
            
#             print(f"      🔍 Fetching Facebook followers for page: {page_id}")
            
#             url = f"{self.facebook_base_url}/{page_id}"
#             params = {
#                 'access_token': access_token,
#                 'fields': 'followers_count'
#             }
            
#             response = requests.get(url, params=params)
#             print(f"      📡 Facebook API response status: {response.status_code}")
            
#             if response.status_code == 200:
#                 data = response.json()
#                 followers_count = data.get('followers_count', 0)
                
#                 print(f"      ✅ Facebook followers retrieved: {followers_count:,}")
#                 return followers_count
#             else:
#                 print(f"      ❌ Followers request failed: {response.status_code}")
#                 print(f"      Response: {response.text}")
#                 return None
                
#         except Exception as e:
#             print(f"      ❌ Error fetching Facebook followers for {page_id}: {e}")
#             return None

#     def update_post_record(self, record_id, metrics, existing_date=None):
#         """Update Facebook post record with Views, Likes, and Date Posted (if not already set)"""
#         try:
#             print(f"      💾 Updating Airtable record: {record_id}")
            
#             # Build update fields
#             update_fields = {
#                 'Views': metrics['views'],
#                 'Likes': metrics['likes']
#             }
            
#             # Handle Date Posted logic (if needed in future)
#             date_action = "none"
#             if existing_date:
#                 date_action = "preserved"
#                 print(f"      📊 Facebook metrics: Views={metrics['views']}, Likes={metrics['likes']}, Date=kept existing ({existing_date})")
#             else:
#                 print(f"      📊 Facebook metrics: Views={metrics['views']}, Likes={metrics['likes']}")
            
#             record = {'fields': update_fields}
            
#             url = f"{self.posts_table_url}/{record_id}"
#             response = requests.patch(url, 
#                                     headers=self.airtable_headers, 
#                                     json=record)
            
#             print(f"      📡 Airtable update response status: {response.status_code}")
            
#             if response.status_code == 200:
#                 print(f"      ✅ Successfully updated Airtable record")
#                 return True, date_action
#             else:
#                 error_data = response.json()
#                 print(f"      ❌ Airtable update failed: {error_data}")
#                 return False, "error"
                
#         except Exception as e:
#             print(f"      ❌ Error updating post record: {e}")
#             return False, "error"

#     def update_channel_followers(self, record_id, follower_count, channel_name):
#         """Update channel record with Facebook followers count"""
#         try:
#             print(f"      💾 Updating Airtable channel record: {record_id}")
            
#             record = {
#                 'fields': {
#                     'Facebook Followers': follower_count
#                 }
#             }
            
#             print(f"      📊 Facebook Followers: {follower_count:,}")
            
#             url = f"{self.channels_table_url}/{record_id}"
#             response = requests.patch(url, 
#                                     headers=self.airtable_headers, 
#                                     json=record)
            
#             print(f"      📡 Airtable update response status: {response.status_code}")
            
#             if response.status_code == 200:
#                 print(f"      ✅ Successfully updated channel followers")
#                 return True
#             else:
#                 error_data = response.json()
#                 print(f"      ❌ Airtable update failed: {error_data}")
#                 return False
                
#         except Exception as e:
#             print(f"      ❌ Error updating channel record: {e}")
#             return False

#     def process_single_facebook_post(self, post_record):
#         """Process a single Facebook post"""
#         record_id = post_record['id']
#         fields = post_record['fields']
        
#         post_name = fields.get('Name', 'Unknown')
#         post_url = fields.get('Link to Post', '')
        
#         # Handle formula errors in Name field
#         if isinstance(post_name, dict) and 'error' in post_name:
#             post_name = f"[Formula Error: {post_name.get('error', 'Unknown')}]"
        
#         print(f"🔍 Processing: {str(post_name)[:60]}...")
#         print(f"   URL: {post_url}")
#         print(f"   Record ID: {record_id}")
        
#         # Extract post ID from URL
#         post_id, page_name = self.extract_facebook_post_info(post_url)
        
#         if not post_id:
#             print(f"   ⚠️ Could not extract post ID from URL")
#             return False
        
#         print(f"   ✅ Extracted post ID: {post_id}")
        
#         # Get metrics
#         print(f"   📊 Processing Facebook post...")
#         metrics = self.get_facebook_post_metrics(post_id)
        
#         if metrics:
#             print(f"   📊 Metrics retrieved successfully")
            
#             # Check for existing Date Posted
#             existing_date = fields.get('Date Posted')
            
#             result = self.update_post_record(record_id, metrics, existing_date)
            
#             if isinstance(result, tuple):
#                 success, date_action = result
#             else:
#                 success, date_action = result, "unknown"
            
#             if success:
#                 print(f"   ✅ Updated: {metrics['views']} views, {metrics['likes']} likes")
#                 return True
#             else:
#                 print(f"   ❌ Failed to update Airtable record")
#                 return False
#         else:
#             print(f"   ⚠️ Could not fetch metrics")
#             return False

#     def process_channel_followers(self, channel_name, channel_record):
#         """Process follower count for a single channel"""
#         record_id = channel_record['record_id']
#         current_followers = channel_record['current_followers']
        
#         print(f"🔍 Processing channel: {channel_name}")
#         print(f"   Current followers in Airtable: {current_followers:,}")
#         print(f"   Record ID: {record_id}")
        
#         # Find corresponding page ID for this channel
#         page_id = None
#         for page in self.facebook_pages:
#             # Map page names to channel names
#             page_name = page.get('page_name', '')
#             if self.map_page_to_channel_name(page_name) == channel_name:
#                 page_id = page.get('page_id')
#                 break
        
#         if not page_id:
#             print(f"   ⚠️ No Facebook page found for channel: {channel_name}")
#             return False
        
#         print(f"   📱 Using Facebook page ID: {page_id}")
        
#         # Get follower count
#         follower_count = self.get_facebook_page_followers(page_id)
        
#         if follower_count is not None:
#             change = follower_count - current_followers
#             if change != 0:
#                 print(f"   Change: {change:+,}")
            
#             success = self.update_channel_followers(record_id, follower_count, channel_name)
            
#             if success:
#                 if change > 0:
#                     print(f"   ✅ Updated: {follower_count:,} followers (+{change:,})")
#                 elif change < 0:
#                     print(f"   ✅ Updated: {follower_count:,} followers ({change:,})")
#                 else:
#                     print(f"   ✅ Updated: {follower_count:,} followers (no change)")
#                 return True
#             else:
#                 print(f"   ❌ Failed to update channel record")
#                 return False
#         else:
#             print(f"   ⚠️ Could not get follower count")
#             return False

#     def map_page_to_channel_name(self, page_name):
#         """Map Facebook page names to Airtable channel names"""
#         # Based on your data, map Facebook page names to Airtable channel names
#         mapping = {
#             'Trading Places Podcast': 'Trading Places',
#             'Good Billionaires': 'Good Billionaires',
#             'Business School': 'Free Business School',
#             'BG2 Clips': 'BG2',
#             'The Techno Optimist': 'Techno Optimist',
#             'Good Politics': 'Good Politics'
#         }
#         return mapping.get(page_name, page_name)

#     def sync_facebook_posts(self):
#         """Sync Facebook post metrics"""
#         print("📘 Starting Facebook Posts Sync...")
#         print("📋 Fetching all posts from Airtable...")
        
#         all_posts = self.get_all_posts_from_airtable()
#         print(f"📊 Found {len(all_posts)} total posts")
        
#         # Filter for Facebook posts only
#         facebook_posts = []
#         for post in all_posts:
#             social_network = post['fields'].get('Social Network', '')
#             post_url = post['fields'].get('Link to Post', '')
            
#             if social_network == 'Facebook' and post_url:
#                 facebook_posts.append(post)
        
#         print(f"🎯 Processing {len(facebook_posts)} Facebook posts")
        
#         if not facebook_posts:
#             print("ℹ️ No Facebook posts found")
#             return {'success': 0, 'skip': 0, 'error': 0}
        
#         # Process each post
#         success_count = 0
#         skip_count = 0
#         error_count = 0
        
#         for i, post in enumerate(facebook_posts, 1):
#             print(f"\n📘 [{i}/{len(facebook_posts)}]", end=" ")
            
#             try:
#                 success = self.process_single_facebook_post(post)
#                 if success:
#                     success_count += 1
#                 else:
#                     skip_count += 1
#             except Exception as e:
#                 print(f"   ❌ Unexpected error: {e}")
#                 error_count += 1
            
#             # Rate limiting
#             time.sleep(0.15)
        
#         # Summary
#         print(f"\n🎉 Facebook posts sync completed!")
#         print(f"✅ Successfully updated: {success_count}")
#         print(f"⏭️ Skipped (no mapping/issues): {skip_count}")
#         print(f"❌ Unexpected errors: {error_count}")
#         print(f"📊 Total processed: {success_count + skip_count + error_count}")
        
#         return {
#             'success': success_count,
#             'skip': skip_count,
#             'error': error_count
#         }

#     def sync_facebook_followers(self):
#         """Sync Facebook follower counts"""
#         print("📘 Starting Facebook Followers Sync...")
        
#         # Build channel mapping
#         self.build_channel_mapping()
        
#         if not self.channel_name_to_record:
#             print("❌ No channels found in Airtable")
#             return {'success': 0, 'skip': 0, 'error': 0}
        
#         # Process channels that have Facebook presence
#         print(f"📊 Processing follower counts...")
        
#         success_count = 0
#         skip_count = 0
#         error_count = 0
#         total_followers = 0
        
#         for channel_name, channel_record in self.channel_name_to_record.items():
#             print(f"\n📘 Processing channel: {channel_name}")
            
#             try:
#                 success = self.process_channel_followers(channel_name, channel_record)
#                 if success:
#                     success_count += 1
#                     # Get follower count for total
#                     page_id = None
#                     for page in self.facebook_pages:
#                         if self.map_page_to_channel_name(page.get('page_name', '')) == channel_name:
#                             page_id = page.get('page_id')
#                             break
#                     if page_id:
#                         followers = self.get_facebook_page_followers(page_id)
#                         if followers:
#                             total_followers += followers
#                 else:
#                     skip_count += 1
#             except Exception as e:
#                 print(f"   ❌ Unexpected error: {e}")
#                 error_count += 1
            
#             time.sleep(0.15)
        
#         # Summary
#         print(f"\n🎉 Facebook followers sync completed!")
#         print(f"✅ Successfully updated: {success_count}")
#         print(f"⏭️ Skipped (no Facebook page): {skip_count}")
#         print(f"❌ Unexpected errors: {error_count}")
#         print(f"📊 Total processed: {success_count + skip_count + error_count}")
        
#         if success_count > 0:
#             print(f"\n📈 Follower Summary:")
#             print(f"👥 Total followers across all channels: {total_followers:,}")
#             print(f"📊 Average followers per channel: {total_followers // success_count:,}")
        
#         return {
#             'success': success_count,
#             'skip': skip_count,
#             'error': error_count,
#             'total_followers': total_followers
#         }

#     def sync_all_facebook(self):
#         """Main function to sync both Facebook posts and followers"""
#         print("="*80)
#         print("STARTING COMPLETE FACEBOOK SYNC")
#         print("="*80)
        
#         if not self.facebook_pages:
#             print("❌ No Facebook pages configured")
#             return
        
#         print(f"📘 Configured Facebook pages:")
#         for page in self.facebook_pages:
#             print(f"   • {page.get('page_name')} (ID: {page.get('page_id')})")
        
#         # Sync posts first
#         posts_results = self.sync_facebook_posts()
        
#         print("\n" + "="*60 + "\n")
        
#         # Sync followers second
#         followers_results = self.sync_facebook_followers()
        
#         # Combined summary
#         print("\n" + "="*80)
#         print("COMPLETE FACEBOOK SYNC SUMMARY")
#         print("="*80)
#         print(f"POSTS:")
#         print(f"  Successfully updated: {posts_results['success']}")
#         print(f"  Skipped: {posts_results['skip']}")
#         print(f"  Errors: {posts_results['error']}")
        
#         print(f"\nFOLLOWERS:")
#         print(f"  Successfully updated: {followers_results['success']}")
#         print(f"  Skipped: {followers_results['skip']}")
#         print(f"  Errors: {followers_results['error']}")
        
#         total_success = posts_results['success'] + followers_results['success']
#         total_processed = (posts_results['success'] + posts_results['skip'] + posts_results['error'] + 
#                           followers_results['success'] + followers_results['skip'] + followers_results['error'])
        
#         print(f"\nOVERALL:")
#         print(f"  Total items processed: {total_processed}")
#         print(f"  Total successful updates: {total_success}")
#         print(f"  Success rate: {(total_success/total_processed*100):.1f}%" if total_processed > 0 else "  Success rate: 0%")
#         print("="*80)

# def main():
#     sync = FacebookSync()
    
#     # Run complete Facebook sync (posts + followers)
#     sync.sync_all_facebook()

# if __name__ == "__main__":
#     main()




import os
import json
import requests
import re
from datetime import datetime
from dotenv import load_dotenv
import time
from urllib.parse import urlparse

# Load environment variables
load_dotenv()

class FacebookSync:
    def __init__(self):
        self.airtable_token = os.getenv('AIRTABLE_PERSONAL_ACCESS_TOKEN')
        self.airtable_base_id = os.getenv('AIRTABLE_BASE_ID')
        self.posts_table_id = os.getenv('AIRTABLE_TABLE_ID', "tblMpYJQjbb5yuKfC")
        self.channels_table_id = os.getenv('AIRTABLE_CHANNELS_TABLE_ID', "tblP8bh3crTHLVoZA")
        
        # Facebook API base URL
        self.facebook_base_url = "https://graph.facebook.com/v21.0"
        
        # Airtable URLs
        self.posts_table_url = f"https://api.airtable.com/v0/{self.airtable_base_id}/{self.posts_table_id}"
        self.channels_table_url = f"https://api.airtable.com/v0/{self.airtable_base_id}/{self.channels_table_id}"
        
        # Headers
        self.airtable_headers = {
            'Authorization': f'Bearer {self.airtable_token}',
            'Content-Type': 'application/json'
        }
        
        # Load Facebook pages from environment
        facebook_pages_json = os.getenv('FACEBOOK_PAGES', '[]')
        # Say what we were given without revealing it: unset vs blank vs shape.
        if 'FACEBOOK_PAGES' not in os.environ:
            print("⚠️ FACEBOOK_PAGES is not set on this service")
        else:
            _raw = os.environ['FACEBOOK_PAGES']
            print(f"ℹ️ FACEBOOK_PAGES is set: {len(_raw)} chars, starts with {_raw.strip()[:2]!r}, {_raw.count(chr(10))} line break(s)")
        try:
            # Take the first JSON document and tolerate anything pasted after it
            # (a stray newline or shell prompt). A strict json.loads rejected the
            # whole list on 2026-09-08 over trailing text and left zero pages.
            cleaned = facebook_pages_json.strip()
            self.facebook_pages, end = json.JSONDecoder().raw_decode(cleaned)
            if cleaned[end:].strip():
                print(f"⚠️ Ignoring {len(cleaned[end:].strip())} chars of trailing text after the FACEBOOK_PAGES JSON")
            print(f"✅ Loaded {len(self.facebook_pages)} Facebook pages from environment")
        except (json.JSONDecodeError, KeyError) as e:
            print(f"❌ Error loading Facebook pages from environment: {e}")
            self.facebook_pages = []
        
        # Build page lookup dictionaries
        self.page_id_to_token = {}
        self.page_name_to_info = {}
        
        for page in self.facebook_pages:
            page_id = page.get('page_id')
            page_name = page.get('page_name')
            page_token = page.get('page_access_token')
            
            if page_id and page_token:
                self.page_id_to_token[page_id] = page_token
            
            if page_name:
                self.page_name_to_info[page_name] = {
                    'page_id': page_id,
                    'page_access_token': page_token
                }

    def resolve_facebook_share_url(self, share_url):
        """Resolve Facebook share URL to get the actual post URL"""
        try:
            print(f"      🔗 Resolving share URL: {share_url}")
            
            response = requests.head(
                share_url, 
                allow_redirects=True, 
                timeout=10,
                headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
                }
            )
            
            final_url = response.url
            print(f"      ✅ Resolved to: {final_url}")
            
            # Validate that we got a proper Facebook URL
            if 'facebook.com' in final_url:
                return final_url
            else:
                print(f"      ⚠️ Resolved URL doesn't contain facebook.com")
                return share_url  # Return original if resolution fails
                
        except requests.RequestException as e:
            print(f"      ❌ Error resolving share URL: {e}")
            return share_url  # Return original URL if resolution fails
        except Exception as e:
            print(f"      ❌ Unexpected error resolving URL: {e}")
            return share_url

    def extract_facebook_post_info(self, url):
        """Extract post ID and page ID from Facebook URL"""
        if not url:
            return None, None
        
        original_url = url
        
        # Check if it's a share URL that needs resolution
        if '/share/v/' in url or '/share/r/' in url:
            print(f"   🔗 Detected share URL, attempting resolution...")
            url = self.resolve_facebook_share_url(url)
            if url != original_url:
                print(f"   ✅ Using resolved URL: {url}")
        
        # Facebook URL patterns
        patterns = [
            # Standard post format: facebook.com/{page-name}/posts/{post-id}
            r'facebook\.com/([^/]+)/posts/(\d+)',
            # Video format: facebook.com/{page-name}/videos/{post-id}
            r'facebook\.com/([^/]+)/videos/(\d+)',
            # Reel format: facebook.com/reel/{post-id}
            r'facebook\.com/reel/(\d+)',
            # Direct post ID format: facebook.com/permalink.php?story_fbid={post-id}&id={page-id}
            r'facebook\.com/permalink\.php\?story_fbid=(\d+)&id=(\d+)',
            # Photo format: facebook.com/{page-name}/photos/{post-id}
            r'facebook\.com/([^/]+)/photos/[^/]+/(\d+)',
            # Generic post ID extraction from resolved URLs
            r'facebook\.com/.*?(\d{15,})',  # Look for long numeric IDs
        ]
        
        for i, pattern in enumerate(patterns):
            match = re.search(pattern, url)
            if match:
                if i == 2:  # Reel format
                    post_id = match.group(1)
                    print(f"   ✅ Extracted reel ID: {post_id}")
                    return post_id, None  # Page ID will be determined later
                elif i == 3:  # Permalink format
                    post_id = match.group(1)
                    page_id = match.group(2)
                    print(f"   ✅ Extracted post ID: {post_id}, page ID: {page_id}")
                    return post_id, page_id
                elif len(match.groups()) == 2:
                    page_name_or_id = match.group(1)
                    post_id = match.group(2)
                    print(f"   ✅ Extracted post ID: {post_id} from page: {page_name_or_id}")
                    return post_id, page_name_or_id
                else:
                    post_id = match.group(1)
                    print(f"   ✅ Extracted post ID: {post_id}")
                    return post_id, None
        
        print(f"   ⚠️ Could not extract post ID from URL: {url}")
        return None, None

    def get_page_access_token_for_post(self, post_id, page_identifier=None):
        """Get the correct page access token for a post"""
        # If we have a page identifier, try to use it
        if page_identifier:
            # Check if it's a direct page ID
            if page_identifier in self.page_id_to_token:
                return self.page_id_to_token[page_identifier]
            
            # Check if it's a page name that maps to our configured pages
            for page in self.facebook_pages:
                if page.get('page_name', '').lower().replace(' ', '') == page_identifier.lower().replace(' ', ''):
                    return page.get('page_access_token')
        
        # If no specific page identified, try each page token until one works
        # This is a fallback for cases where we can't determine the page from URL
        for page in self.facebook_pages:
            token = page.get('page_access_token')
            if token and self.test_post_access(post_id, token):
                return token
        
        return None

    def test_post_access(self, post_id, access_token):
        """Test if a post can be accessed with a specific token"""
        try:
            url = f"{self.facebook_base_url}/{post_id}"
            params = {'access_token': access_token, 'fields': 'id'}
            
            response = requests.get(url, params=params, timeout=5)
            return response.status_code == 200
        except:
            return False

    def format_facebook_date(self, facebook_date_str):
        """Convert Facebook ISO date to Airtable date format"""
        try:
            # Facebook returns: "2025-01-15T14:30:00+0000"
            dt = datetime.fromisoformat(facebook_date_str.replace('+0000', '+00:00'))
            return dt.strftime('%Y-%m-%d')
        except Exception as e:
            print(f"      ⚠️ Error formatting date {facebook_date_str}: {e}")
            return None

    def get_facebook_post_metrics(self, post_id, access_token):
        """Get Facebook post metrics (views and likes)"""
        try:
            print(f"      Fetching Facebook metrics for post: {post_id}")

            # One request per post: the video node carries plays, likes and the
            # publish time. The old two-metric video_insights call
            # (post_video_likes_by_reaction_type + fb_reels_total_plays) makes
            # Meta answer {"data": []} for the whole request; the reaction
            # metric is the poison pill. Probed 2026-09-09: `views` here equals
            # fb_reels_total_plays exactly (17,357 on the test reel).
            views = None
            likes = None
            date_posted = None

            node = requests.get(
                f"{self.facebook_base_url}/{post_id}",
                params={'access_token': access_token,
                        'fields': 'views,likes.summary(true),created_time'},
                timeout=30,
            )
            print(f"      Video node response status: {node.status_code}")
            if node.status_code == 200:
                data = node.json()
                views = data.get('views')
                likes = data.get('likes', {}).get('summary', {}).get('total_count')
                if data.get('created_time'):
                    date_posted = self.format_facebook_date(data['created_time'])
            else:
                print(f"      Video node failed: {node.status_code} {node.text[:300]}")

            if views is None:
                # Fallback: the one insights metric that answers on its own.
                ins = requests.get(
                    f"{self.facebook_base_url}/{post_id}/video_insights",
                    params={'access_token': access_token, 'metric': 'fb_reels_total_plays'},
                    timeout=30,
                )
                print(f"      video_insights fb_reels_total_plays status: {ins.status_code}")
                if ins.status_code == 200:
                    for insight in ins.json().get('data', []):
                        vals = insight.get('values') or []
                        if insight.get('name') == 'fb_reels_total_plays' and vals:
                            views = vals[0].get('value')
                if views is None:
                    print("      No play count obtainable; leaving Views unchanged")

            if likes is None:
                # Nothing usable came back at all; do not write for this post.
                print("      No like count obtainable; skipping this post")
                return None

            metrics = {
                'views': views,
                'likes': likes,
                'date_posted': date_posted
            }
            
            print(f"      ✅ Facebook metrics retrieved: Views={views}, Likes={likes}, Date={date_posted}")
            return metrics
            
        except Exception as e:
            print(f"      ❌ Error fetching Facebook metrics for {post_id}: {e}")
            return None

    def get_facebook_page_followers(self, page_id, access_token):
        """Get Facebook page follower count"""
        try:
            print(f"      🔍 Fetching followers for page: {page_id}")
            
            url = f"{self.facebook_base_url}/{page_id}"
            params = {
                'access_token': access_token,
                'fields': 'followers_count'
            }
            
            response = requests.get(url, params=params)
            print(f"      📡 Page followers API response status: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                followers_count = data.get('followers_count', 0)
                print(f"      ✅ Facebook followers: {followers_count:,}")
                return followers_count
            else:
                print(f"      ❌ Failed to get followers: {response.status_code}")
                print(f"      Response: {response.text}")
                return None
                
        except Exception as e:
            print(f"      ❌ Error fetching Facebook followers for {page_id}: {e}")
            return None

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

    def update_post_record(self, record_id, metrics, existing_date=None):
        """Update Facebook post record with Views, Likes, and Date Posted (if not already set)"""
        try:
            print(f"      💾 Updating Airtable record: {record_id}")
            
            # Build update fields
            # A failed insights call (e.g. a token without read_insights) must not
            # overwrite a real view count with 0. On 2026-09-08 exactly that zeroed
            # 78 posts before the run was cancelled.
            update_fields = {'Likes': metrics['likes']}
            if metrics.get('views') is not None:
                update_fields['Views'] = metrics['views']
            
            # Handle Date Posted logic
            date_action = "none"
            api_date = metrics.get('date_posted')
            
            if existing_date:
                # Keep existing manual date
                date_action = "preserved"
                print(f"      📊 Facebook metrics: Views={metrics['views']}, Likes={metrics['likes']}, Date=kept existing ({existing_date})")
            elif api_date:
                # Add new date from API
                update_fields['Date Posted'] = api_date
                date_action = "added"
                print(f"      📊 Facebook metrics: Views={metrics['views']}, Likes={metrics['likes']}, Date=added ({api_date})")
            else:
                # No date available from API
                date_action = "unavailable"
                print(f"      📊 Facebook metrics: Views={metrics['views']}, Likes={metrics['likes']}, Date=not available from API")
            
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

    def update_channel_followers(self, record_id, followers_count, channel_name):
        """Update channel record with Facebook followers count"""
        try:
            print(f"      💾 Updating channel record: {record_id}")
            
            record = {
                'fields': {
                    'Facebook Followers': followers_count
                }
            }
            
            print(f"      📊 Facebook Followers: {followers_count:,}")
            
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

    def process_single_facebook_post(self, post_record):
        """Process a single Facebook post"""
        record_id = post_record['id']
        fields = post_record['fields']
        
        post_name = fields.get('Name', 'Unknown')
        if isinstance(post_name, dict) and 'error' in post_name:
            post_name = f"[Formula Error: {post_name.get('error', 'Unknown')}]"
        
        post_url = fields.get('Link to Post', '')
        
        print(f"🔍 Processing: {str(post_name)[:60]}...")
        print(f"   URL: {post_url}")
        print(f"   Record ID: {record_id}")
        
        # Extract post ID and page info from URL
        post_id, page_identifier = self.extract_facebook_post_info(post_url)
        
        if not post_id:
            print(f"   ⚠️ Could not extract post ID from URL")
            return False
        
        print(f"   ✅ Extracted post ID: {post_id}")
        
        # Get the correct page access token
        access_token = self.get_page_access_token_for_post(post_id, page_identifier)
        
        if not access_token:
            print(f"   ⚠️ Could not find valid access token for post")
            return False
        
        print(f"   🔗 Using page access token")
        
        # Get post metrics
        if os.getenv('FB_PROBE'):
            # Diagnostic only: ask Meta about this one object several ways, print
            # every body (bodies never contain the token), write nothing, stop.
            base = self.facebook_base_url
            page_id = getattr(self, '_probe_page_id', None)
            variants = [
                ("video_insights, no metric filter", f"{base}/{post_id}/video_insights", {}),
                ("video_insights fb_reels_total_plays", f"{base}/{post_id}/video_insights", {'metric': 'fb_reels_total_plays'}),
                ("video_insights total_video_views", f"{base}/{post_id}/video_insights", {'metric': 'total_video_views'}),
                ("video_insights blue_reels_play_count", f"{base}/{post_id}/video_insights", {'metric': 'blue_reels_play_count'}),
                ("video node fields", f"{base}/{post_id}", {'fields': 'id,title,length,views,created_time,from,status'}),
                ("post insights edge", f"{base}/{post_id}/insights", {'metric': 'post_impressions_unique,post_video_views'}),
            ]
            if page_id:
                variants.append(("page-scoped post insights", f"{base}/{page_id}_{post_id}/insights", {'metric': 'post_impressions_unique,post_video_views'}))
            print(f"🧪 PROBE for {post_id} via {base}")
            for label, url, params in variants:
                try:
                    r = requests.get(url, params={**params, 'access_token': access_token}, timeout=30)
                    print(f"   [{r.status_code}] {label}: {r.text[:400]}")
                except Exception as exc:
                    print(f"   [ERR] {label}: {exc}")
            print("🧪 PROBE done; exiting without writing")
            raise SystemExit(0)
        metrics = self.get_facebook_post_metrics(post_id, access_token)
        
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

    def sync_facebook_posts(self):
        """Sync Facebook post metrics"""
        print("📘 Starting Facebook Posts Sync...")
        print("📋 Fetching all posts from Airtable...")
        
        all_posts = self.get_all_posts_from_airtable()
        print(f"📊 Found {len(all_posts)} total posts")
        
        # Filter for Facebook posts only
        facebook_posts = []
        for post in all_posts:
            social_network = post['fields'].get('Social Network', '')
            post_url = post['fields'].get('Link to Post', '')
            
            if social_network == 'Facebook' and post_url:
                facebook_posts.append(post)
        
        max_posts = int(os.getenv('FB_MAX_POSTS', '0') or 0)
        
        if max_posts > 0:
        
            facebook_posts = facebook_posts[:max_posts]
        
            print(f"FB_MAX_POSTS={max_posts}: limiting this run to the first {len(facebook_posts)} post(s)")
        
        print(f"🎯 Processing {len(facebook_posts)} Facebook posts")
        
        if not facebook_posts:
            print("ℹ️ No Facebook posts found")
            return {'success': 0, 'skip': 0, 'error': 0}
        
        # Analyze existing dates
        posts_with_dates = sum(1 for post in facebook_posts if post['fields'].get('Date Posted'))
        posts_without_dates = len(facebook_posts) - posts_with_dates
        
        print(f"📅 Date Analysis:")
        print(f"   Posts with existing dates: {posts_with_dates} (will be preserved)")
        print(f"   Posts without dates: {posts_without_dates} (will get API dates)")
        
        # Process each post
        success_count = 0
        skip_count = 0
        error_count = 0
        
        for i, post in enumerate(facebook_posts, 1):
            print(f"\n📘 [{i}/{len(facebook_posts)}]", end=" ")
            
            try:
                success = self.process_single_facebook_post(post)
                if success:
                    success_count += 1
                else:
                    skip_count += 1
                    
            except Exception as e:
                print(f"   ❌ Unexpected error: {e}")
                error_count += 1
            
            # Rate limiting
            time.sleep(0.15)
        
        print(f"\n🎉 Facebook posts sync completed!")
        print(f"✅ Successfully updated: {success_count}")
        print(f"⏭️ Skipped (no mapping/issues): {skip_count}")
        print(f"❌ Unexpected errors: {error_count}")
        print(f"📊 Total processed: {success_count + skip_count + error_count}")
        
        return {
            'success': success_count,
            'skip': skip_count,
            'error': error_count
        }

    def sync_facebook_followers(self):
        """Sync Facebook follower counts for all channels"""
        print("\n📘 Starting Facebook Followers Sync...")
        print("📋 Fetching all channels from Airtable...")
        
        all_channels = self.get_all_channels_from_airtable()
        print(f"📊 Found {len(all_channels)} total channels")
        
        # Map channel names to records and find Facebook channels
        channel_mapping = {}
        facebook_channels = []
        
        for channel in all_channels:
            fields = channel['fields']
            channel_name = fields.get('Channel', '').strip()
            facebook_profile = fields.get('Facebook Profile', '').strip()
            current_followers = fields.get('Facebook Followers', 0)
            
            if channel_name:
                channel_mapping[channel_name] = {
                    'record_id': channel['id'],
                    'facebook_profile': facebook_profile,
                    'current_followers': current_followers
                }
                
                # Check if this channel has a corresponding Facebook page
                if channel_name in self.page_name_to_info:
                    facebook_channels.append({
                        'channel_name': channel_name,
                        'record_info': channel_mapping[channel_name],
                        'page_info': self.page_name_to_info[channel_name]
                    })
                    print(f"   • {channel_name} → {current_followers:,} followers")
        
        print(f"🎯 Processing {len(facebook_channels)} channels with Facebook pages")
        
        if not facebook_channels:
            print("ℹ️ No channels with Facebook pages found")
            return {'success': 0, 'skip': 0, 'error': 0}
        
        # Process each channel
        success_count = 0
        skip_count = 0
        error_count = 0
        
        for i, channel_info in enumerate(facebook_channels, 1):
            channel_name = channel_info['channel_name']
            record_info = channel_info['record_info']
            page_info = channel_info['page_info']
            
            print(f"\n📘 [{i}/{len(facebook_channels)}] Processing: {channel_name}")
            
            page_id = page_info['page_id']
            access_token = page_info['page_access_token']
            current_followers = record_info['current_followers']
            
            try:
                followers_count = self.get_facebook_page_followers(page_id, access_token)
                
                if followers_count is not None:
                    success = self.update_channel_followers(record_info['record_id'], followers_count, channel_name)
                    
                    if success:
                        change = followers_count - current_followers
                        if change > 0:
                            print(f"   ✅ Updated: {followers_count:,} followers (+{change:,})")
                        elif change < 0:
                            print(f"   ✅ Updated: {followers_count:,} followers ({change:,})")
                        else:
                            print(f"   ✅ Updated: {followers_count:,} followers (no change)")
                        success_count += 1
                    else:
                        print(f"   ❌ Failed to update Airtable record")
                        error_count += 1
                else:
                    print(f"   ⚠️ Could not fetch follower count")
                    skip_count += 1
                    
            except Exception as e:
                print(f"   ❌ Unexpected error: {e}")
                error_count += 1
            
            # Rate limiting
            time.sleep(0.15)
        
        print(f"\n🎉 Facebook followers sync completed!")
        print(f"✅ Successfully updated: {success_count}")
        print(f"⏭️ Skipped (no followers/issues): {skip_count}")
        print(f"❌ Unexpected errors: {error_count}")
        print(f"📊 Total processed: {success_count + skip_count + error_count}")
        
        return {
            'success': success_count,
            'skip': skip_count,
            'error': error_count
        }

    def sync_all_facebook(self):
        """Main function to sync both Facebook posts and followers"""
        print("=" * 80)
        print("STARTING COMPLETE FACEBOOK SYNC")
        print("=" * 80)
        
        # Check if we have Facebook pages configured
        if not self.facebook_pages:
            print("❌ No Facebook pages configured")
            return
        
        # Sync posts first
        posts_results = self.sync_facebook_posts()
        
        print("\n" + "=" * 60 + "\n")
        
        # Sync followers second
        followers_results = self.sync_facebook_followers()
        
        # Combined summary
        print("\n" + "=" * 80)
        print("COMPLETE FACEBOOK SYNC SUMMARY")
        print("=" * 80)
        print(f"POSTS:")
        print(f"  Successfully updated: {posts_results['success']}")
        print(f"  Skipped: {posts_results['skip']}")
        print(f"  Errors: {posts_results['error']}")
        
        print(f"\nFOLLOWERS:")
        print(f"  Successfully updated: {followers_results['success']}")
        print(f"  Skipped: {followers_results['skip']}")
        print(f"  Errors: {followers_results['error']}")
        
        total_success = posts_results['success'] + followers_results['success']
        total_processed = (posts_results['success'] + posts_results['skip'] + posts_results['error'] + 
                          followers_results['success'] + followers_results['skip'] + followers_results['error'])
        
        print(f"\nOVERALL:")
        print(f"  Total items processed: {total_processed}")
        print(f"  Total successful updates: {total_success}")
        print(f"  Success rate: {(total_success/total_processed*100):.1f}%" if total_processed > 0 else "  Success rate: 0%")
        print("=" * 80)

def main():
    sync = FacebookSync()
    
    # Run complete Facebook sync (posts + followers)
    sync.sync_all_facebook()

if __name__ == "__main__":
    main()