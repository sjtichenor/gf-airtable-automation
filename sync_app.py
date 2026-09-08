import os
import json
import requests
import re
from datetime import datetime
from dotenv import load_dotenv
import time

# Load environment variables
load_dotenv()

class PostsTableSync:

    def __init__(self):
        self.youtube_api_key = os.getenv('YOUTUBE_API_KEY')
        self.meta_access_token = os.getenv('META_ACCESS_TOKEN')
        self.airtable_token = os.getenv('AIRTABLE_PERSONAL_ACCESS_TOKEN')
        self.airtable_base_id = os.getenv('AIRTABLE_BASE_ID')
        self.posts_table_id = os.getenv('AIRTABLE_TABLE_ID', "tblMpYJQjbb5yuKfC")  # Fallback to default
        self.channels_table_id = os.getenv('AIRTABLE_CHANNELS_TABLE_ID', "tblP8bh3crTHLVoZA")  # Fallback to default
        
        # API base URLs
        self.youtube_base_url = os.getenv('YOUTUBE_API_BASE_URL', "https://www.googleapis.com/youtube/v3")
        self.meta_base_url = os.getenv('META_API_BASE_URL', "https://graph.facebook.com/v23.0")
        
        # Airtable URLs
        self.posts_table_url = f"https://api.airtable.com/v0/{self.airtable_base_id}/{self.posts_table_id}"
        self.channels_table_url = f"https://api.airtable.com/v0/{self.airtable_base_id}/{self.channels_table_id}"
        
        # Headers
        self.airtable_headers = {
            'Authorization': f'Bearer {self.airtable_token}',
            'Content-Type': 'application/json'
        }
        
        # Load Instagram account mapping from environment
        instagram_accounts_json = os.getenv('INSTAGRAM_ACCOUNTS', '[]')
        try:
            instagram_accounts = json.loads(instagram_accounts_json)
            self.username_to_account_id = {
                account['username'].replace('@', ''): account['account_id'] 
                for account in instagram_accounts
            }
        except (json.JSONDecodeError, KeyError) as e:
            print(f"⚠️ Error loading Instagram accounts from environment: {e}")
            # Fallback to hardcoded mapping
            self.username_to_account_id = {
                "free.business.school": "17841474942088392",
                "techno.optimist.prime": "17841475373332532", 
                "bg2clips": "17841474088665951",
                "real.good.politics": "17841466892907990"
            }
        
        # Cache for channel records to avoid repeated API calls
        self.channel_cache = {}

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

    def get_channel_record(self, channel_id):
        """Get channel record from Channels table with caching"""
        if channel_id in self.channel_cache:
            return self.channel_cache[channel_id]
        
        url = f"{self.channels_table_url}/{channel_id}"
        response = requests.get(url, headers=self.airtable_headers)
        
        if response.status_code == 200:
            channel_record = response.json()
            self.channel_cache[channel_id] = channel_record['fields']
            return channel_record['fields']
        else:
            print(f"⚠️ Error fetching channel {channel_id}: {response.json()}")
            return None

    def extract_platform_and_id(self, post_url):
        """Extract platform and video/post ID from URL"""
        if not post_url:
            return None, None
            
        post_url = post_url.strip()
        
        # YouTube patterns
        youtube_patterns = [
            r'youtube\.com/watch\?v=([^&?]+)',
            r'youtube\.com/shorts/([^?&/]+)',
            r'youtu\.be/([^?&/]+)'
        ]
        
        for pattern in youtube_patterns:
            match = re.search(pattern, post_url)
            if match:
                return 'YouTube', match.group(1)
        
        # Instagram patterns - improved to handle various formats
        instagram_patterns = [
            # Standard formats
            r'instagram\.com/p/([^/?&]+)',
            r'instagram\.com/reel/([^/?&]+)',
            # Handle username in path: instagram.com/username/p/shortcode/ or instagram.com/username/reel/shortcode/
            r'instagram\.com/[^/]+/p/([^/?&]+)',
            r'instagram\.com/[^/]+/reel/([^/?&]+)',
            # Handle with additional parameters
            r'instagram\.com/p/([^/?&]+)[/?&]',
            r'instagram\.com/reel/([^/?&]+)[/?&]'
        ]
        
        for pattern in instagram_patterns:
            match = re.search(pattern, post_url)
            if match:
                return 'Instagram', match.group(1)
        
        return None, None

    def get_instagram_account_id_for_post(self, post_record):
        """Get Instagram account ID for a post using channel mapping"""
        # Get channel ID from post
        channel_ids = post_record.get('Social Media Accounts', [])
        if not channel_ids:
            print("  ⚠️ No channel found for post - skipping")
            return None
        
        # Get channel record
        channel_record = self.get_channel_record(channel_ids[0])
        if not channel_record:
            print("  ⚠️ Could not fetch channel record - skipping")
            return None
        
        # Get Instagram profile URL
        ig_profile = channel_record.get('IG Profile', '')
        if not ig_profile:
            print("  ⚠️ No Instagram profile found for channel - skipping")
            return None
        
        # Extract username from IG profile URL
        # https://www.instagram.com/real.good.politics → real.good.politics
        try:
            username = ig_profile.split('instagram.com/')[-1].rstrip('/')
            account_id = self.username_to_account_id.get(username)
            
            if not account_id:
                print(f"  ⚠️ Account '{username}' not in mapping - skipping this post")
                return None
                
            return account_id
            
        except Exception as e:
            print(f"  ⚠️ Error extracting username from {ig_profile} - skipping: {e}")
            return None

    def get_youtube_video_metrics(self, video_id):
        """Get YouTube video metrics"""
        try:
            print(f"      🔍 Fetching YouTube metrics for video: {video_id}")
            url = f"{self.youtube_base_url}/videos"
            params = {
                'key': self.youtube_api_key,
                'id': video_id,
                'part': 'statistics'
            }
            
            response = requests.get(url, params=params)
            print(f"      📡 YouTube API response status: {response.status_code}")
            data = response.json()
            
            if 'items' not in data or len(data['items']) == 0:
                print(f"      ⚠️ YouTube video not found: {video_id}")
                print(f"      📄 API response: {data}")
                return None
            
            stats = data['items'][0]['statistics']
            metrics = {
                'views': int(stats.get('viewCount', 0)),
                'likes': int(stats.get('likeCount', 0))
            }
            print(f"      ✅ YouTube metrics retrieved: {metrics}")
            return metrics
            
        except Exception as e:
            print(f"      ❌ Error fetching YouTube metrics for {video_id}: {e}")
            return None

    def get_instagram_media_id_from_shortcode(self, account_id, shortcode):
        """Get Instagram media ID from shortcode by searching account media"""
        try:
            print(f"      🔍 Searching for shortcode '{shortcode}' in account {account_id}")
            # Get all media from account
            url = f"{self.meta_base_url}/{account_id}/media"
            params = {
                'access_token': self.meta_access_token,
                'fields': 'id,permalink'
            }
            
            page_count = 0
            # Search through pages to find matching shortcode
            while url and page_count < 10:  # Limit to 10 pages for debugging
                page_count += 1
                print(f"      📄 Searching page {page_count} of account media...")
                
                response = requests.get(url, params=params)
                print(f"      📡 Instagram media API response status: {response.status_code}")
                data = response.json()
                
                if 'data' not in data:
                    print(f"      ⚠️ No data in response: {data}")
                    break
                
                print(f"      📊 Found {len(data['data'])} media items on this page")
                
                for i, media in enumerate(data['data']):
                    permalink = media.get('permalink', '')
                    media_id = media.get('id', '')
                    print(f"      🔗 Media {i+1}: ID={media_id}, URL={permalink}")
                    
                    if f'/p/{shortcode}' in permalink or f'/reel/{shortcode}' in permalink:
                        print(f"      ✅ Found matching media! ID: {media_id}")
                        return media_id
                
                # Get next page
                url = data.get('paging', {}).get('next')
                params = None  # URL already contains params
                time.sleep(0.5)  # Rate limiting
            
            print(f"      ❌ Instagram media not found for shortcode: {shortcode} (searched {page_count} pages)")
            return None
            
        except Exception as e:
            print(f"      ❌ Error finding Instagram media ID for {shortcode}: {e}")
            return None

    def get_instagram_media_metrics(self, media_id):
        """Get Instagram media metrics"""
        try:
            print(f"      🔍 Fetching Instagram insights for media: {media_id}")
            url = f"{self.meta_base_url}/{media_id}/insights"
            params = {
                'metric': 'likes,comments,reach,shares,saved,total_interactions,views',
                'access_token': self.meta_access_token
            }
            
            response = requests.get(url, params=params)
            print(f"      📡 Instagram insights API response status: {response.status_code}")
            data = response.json()
            
            if 'data' not in data:
                print(f"      ⚠️ No insights data for Instagram media: {media_id}")
                print(f"      📄 API response: {data}")
                return None
            
            # Convert insights array to dictionary
            metrics = {}
            for insight in data['data']:
                metric_name = insight['name']
                metric_value = insight['values'][0]['value'] if insight['values'] else 0
                metrics[metric_name] = metric_value
                print(f"      📊 {metric_name}: {metric_value}")
            
            result = {
                'views': metrics.get('views', 0),
                'reach': metrics.get('reach', 0),
                'likes': metrics.get('likes', 0)
            }
            print(f"      ✅ Instagram metrics retrieved: {result}")
            return result
            
        except Exception as e:
            print(f"      ❌ Error fetching Instagram metrics for {media_id}: {e}")
            return None

    def update_post_record(self, record_id, platform, metrics):
        """Update post record with fresh metrics (platform-specific fields)"""
        try:
            print(f"      💾 Updating Airtable record: {record_id}")
            print(f"      🏷️ Platform: {platform}")
            
            # Platform-specific field updates
            if platform == 'YouTube':
                record = {
                    'fields': {
                        'Views': metrics['views'],
                        'Likes': metrics['likes']
                    }
                }
                print(f"      📊 YouTube metrics: Views={metrics['views']}, Likes={metrics['likes']}")
                
            elif platform == 'Instagram':
                record = {
                    'fields': {
                        'Views': metrics['views'],
                        'Reach': metrics['reach'],
                        'Likes': metrics['likes']
                    }
                }
                # print(f"      📊 Instagram metrics: Reach={metrics['reach']}, Likes={metrics['likes']}")
                print(f"Instagram metrics: Views={metrics['views']}, Reach={metrics['reach']}, Likes={metrics['likes']}")
            
            else:
                print(f"      ⚠️ Unknown platform: {platform}")
                return False
            
            url = f"{self.posts_table_url}/{record_id}"
            response = requests.patch(url, 
                                    headers=self.airtable_headers, 
                                    json=record)
            
            print(f"      📡 Airtable update response status: {response.status_code}")
            
            if response.status_code == 200:
                print(f"      ✅ Successfully updated Airtable record")
                return True
            else:
                error_data = response.json()
                print(f"      ❌ Airtable update failed: {error_data}")
                print(f"      🔍 Request payload: {record}")
                return False
                
        except Exception as e:
            print(f"      ❌ Error updating post record: {e}")
            return False

    def process_single_post(self, post_record):
        """Process a single post record"""
        record_id = post_record['id']
        fields = post_record['fields']
        
        # Get post details
        post_name = fields.get('Name', 'Unknown')
        post_url = fields.get('Link to Post', '')
        social_network = fields.get('Social Network', '')
        
        print(f"🔍 Processing: {post_name}")
        print(f"   Platform: {social_network} | URL: {post_url}")
        print(f"   Record ID: {record_id}")
        
        # Skip if not YouTube or Instagram
        if social_network not in ['YouTube', 'Instagram']:
            print(f"   ⏭️ Skipping {social_network} (not supported)")
            return False
        
        # Extract platform and ID from URL
        detected_platform, content_id = self.extract_platform_and_id(post_url)
        
        if not detected_platform or not content_id:
            print(f"   ⚠️ Could not extract ID from URL - skipping")
            print(f"   🔍 URL parsing result: platform={detected_platform}, content_id={content_id}")
            return False
        
        print(f"   ✅ Extracted content ID: {content_id}")
        
        # Verify platform matches
        if detected_platform != social_network:
            print(f"   ⚠️ Platform mismatch: {social_network} vs {detected_platform} - skipping")
            return False
        
        # Get metrics based on platform
        metrics = None
        
        if social_network == 'YouTube':
            print(f"   🎥 Processing YouTube video...")
            metrics = self.get_youtube_video_metrics(content_id)
            
        elif social_network == 'Instagram':
            print(f"   📱 Processing Instagram post...")
            # Get Instagram account ID from channel mapping
            account_id = self.get_instagram_account_id_for_post(fields)
            if not account_id:
                return False  # Skip if no account mapping
            
            print(f"   🔗 Using Instagram account ID: {account_id}")
            
            # Get media ID from shortcode
            media_id = self.get_instagram_media_id_from_shortcode(account_id, content_id)
            if not media_id:
                return False  # Skip if can't find media
            
            # Get metrics
            metrics = self.get_instagram_media_metrics(media_id)
        
        # Update record if we got metrics
        if metrics:
            print(f"   📊 Metrics retrieved successfully")
            success = self.update_post_record(record_id, social_network, metrics)
            if success:
                if social_network == 'YouTube':
                    print(f"   ✅ Updated: {metrics['views']} views, {metrics['likes']} likes")
                elif social_network == 'Instagram':
                    print(f"Updated: {metrics['views']} views, {metrics['reach']} reach, {metrics['likes']} likes")
                    # print(f"   ✅ Updated: {metrics['reach']} reach, {metrics['likes']} likes")
                return True
            else:
                print(f"   ❌ Failed to update Airtable record")
                return False
        else:
            print(f"   ⚠️ Could not fetch metrics - skipping")
            return False

    def sync_all_posts(self):
        """Main function to sync all posts"""
        print("🚀 Starting Posts Table Sync...")
        print("📋 Fetching all posts from Airtable...")
        
        all_posts = self.get_all_posts_from_airtable()
        print(f"📊 Found {len(all_posts)} total posts")
        
        # Filter for YouTube and Instagram posts only
        target_posts = []
        for post in all_posts:
            social_network = post['fields'].get('Social Network', '')
            if social_network in ['YouTube', 'Instagram']:
            
                target_posts.append(post)
        
        print(f"🎯 Processing {len(target_posts)} YouTube/Instagram posts")
        
        # Process each post
        success_count = 0
        skip_count = 0
        error_count = 0
        
        for i, post in enumerate(target_posts, 1):
            print(f"\n📱 [{i}/{len(target_posts)}]", end=" ")
            
            try:
                success = self.process_single_post(post)
                if success:
                    success_count += 1
                else:
                    skip_count += 1
                    
            except Exception as e:
                print(f"   ❌ Unexpected error: {e}")
                error_count += 1
            
            # Rate limiting
            time.sleep(0.5)
        
        # Summary
        print(f"\n🎉 Sync completed!")
        print(f"✅ Successfully updated: {success_count}")
        print(f"⏭️ Skipped (no mapping/issues): {skip_count}")
        print(f"❌ Unexpected errors: {error_count}")
        print(f"📊 Total processed: {success_count + skip_count + error_count}")
        
        if skip_count > 0:
            print(f"\n💡 To reduce skipped posts:")
            print(f"   • Add missing Instagram account mappings to .env")
            print(f"   • Fix posts with missing Channel field")
            print(f"   • Check URLs for platform mismatches")

    def sync_recent_posts(self, days_back=7):
        """Sync only recent posts (for daily runs)"""
        print(f"🚀 Starting Recent Posts Sync (last {days_back} days)...")
        
        all_posts = self.get_all_posts_from_airtable()
        
        # Filter for recent posts
        cutoff_date = datetime.now()
        recent_posts = []
        
        for post in all_posts:
            fields = post['fields']
            social_network = fields.get('Social Network', '')
            
            # Only YouTube/Instagram
            if social_network not in ['YouTube', 'Instagram']:
                continue
            
            # Check if recent (you might want to add date filtering logic here)
            # For now, we'll process all YouTube/Instagram posts
            recent_posts.append(post)
        
        print(f"🎯 Processing {len(recent_posts)} recent posts")
        
        # Process posts
        success_count = 0
        for i, post in enumerate(recent_posts, 1):
            print(f"\n📱 [{i}/{len(recent_posts)}]", end=" ")
            
            try:
                success = self.process_single_post(post)
                if success:
                    success_count += 1
            except Exception as e:
                print(f"   ❌ Error: {e}")
            
            time.sleep(0.5)
        
        print(f"\n✅ Updated {success_count} recent posts")

    # FOLLOWER COUNT SYNC METHODS

    def extract_youtube_channel_id_from_url(self, youtube_url):
        """Extract YouTube channel ID from various YouTube URL formats"""
        if not youtube_url:
            return None
            
        youtube_url = youtube_url.strip()
        
        # Direct channel ID format: youtube.com/channel/UC...
        channel_id_match = re.search(r'youtube\.com/channel/([^/?&]+)', youtube_url)
        if channel_id_match:
            return channel_id_match.group(1)
        
        # Custom URL format: youtube.com/c/channelname or youtube.com/@channelname
        custom_url_patterns = [
            r'youtube\.com/c/([^/?&]+)',
            r'youtube\.com/@([^/?&]+)',
            r'youtube\.com/user/([^/?&]+)'
        ]
        
        for pattern in custom_url_patterns:
            match = re.search(pattern, youtube_url)
            if match:
                custom_name = match.group(1)
                # Need to resolve custom name to channel ID using YouTube API
                return self.resolve_youtube_channel_id_by_name(custom_name)
        
        return None

    def resolve_youtube_channel_id_by_name(self, channel_name):
        """Resolve YouTube channel custom name/handle to channel ID"""
        try:
            print(f"      🔍 Resolving YouTube channel name: {channel_name}")
            
            # Try search API to find channel by name
            url = f"{self.youtube_base_url}/search"
            params = {
                'key': self.youtube_api_key,
                'q': channel_name,
                'type': 'channel',
                'part': 'snippet',
                'maxResults': 1
            }
            
            response = requests.get(url, params=params)
            print(f"      📡 YouTube search API response status: {response.status_code}")
            data = response.json()
            
            if 'items' in data and len(data['items']) > 0:
                channel_id = data['items'][0]['snippet']['channelId']
                print(f"      ✅ Resolved channel ID: {channel_id}")
                return channel_id
            else:
                print(f"      ⚠️ Could not resolve channel name: {channel_name}")
                return None
                
        except Exception as e:
            print(f"      ❌ Error resolving channel name {channel_name}: {e}")
            return None

    def get_youtube_subscriber_count(self, channel_id):
        """Get YouTube channel subscriber count"""
        try:
            print(f"      🔍 Fetching YouTube subscriber count for channel: {channel_id}")
            url = f"{self.youtube_base_url}/channels"
            params = {
                'key': self.youtube_api_key,
                'id': channel_id,
                'part': 'statistics'
            }
            
            response = requests.get(url, params=params)
            print(f"      📡 YouTube channels API response status: {response.status_code}")
            data = response.json()
            
            if 'items' not in data or len(data['items']) == 0:
                print(f"      ⚠️ YouTube channel not found: {channel_id}")
                print(f"      📄 API response: {data}")
                return None
            
            stats = data['items'][0]['statistics']
            # Note: subscriberCount might be hidden by channel owner
            subscriber_count = stats.get('subscriberCount')
            
            if subscriber_count is None:
                print(f"      ⚠️ Subscriber count is hidden for channel: {channel_id}")
                return None
            
            subscriber_count = int(subscriber_count)
            print(f"      ✅ YouTube subscriber count: {subscriber_count:,}")
            return subscriber_count
            
        except Exception as e:
            print(f"      ❌ Error fetching YouTube subscriber count for {channel_id}: {e}")
            return None

    def get_instagram_follower_count(self, account_id):
        """Get Instagram account follower count"""
        try:
            print(f"      🔍 Fetching Instagram follower count for account: {account_id}")
            url = f"{self.meta_base_url}/{account_id}"
            params = {
                'access_token': self.meta_access_token,
                'fields': 'followers_count'
            }
            
            response = requests.get(url, params=params)
            print(f"      📡 Instagram account API response status: {response.status_code}")
            data = response.json()
            
            if 'followers_count' not in data:
                print(f"      ⚠️ No follower count data for Instagram account: {account_id}")
                print(f"      📄 API response: {data}")
                return None
            
            follower_count = data['followers_count']
            print(f"      ✅ Instagram follower count: {follower_count:,}")
            return follower_count
            
        except Exception as e:
            print(f"      ❌ Error fetching Instagram follower count for {account_id}: {e}")
            return None

    def extract_instagram_username_from_url(self, ig_url):
        """Extract Instagram username from profile URL"""
        if not ig_url:
            return None
            
        try:
            # https://www.instagram.com/username → username
            username = ig_url.split('instagram.com/')[-1].rstrip('/')
            return username
        except Exception as e:
            print(f"      ⚠️ Error extracting username from {ig_url}: {e}")
            return None

    def update_channel_follower_counts(self, channel_record_id, youtube_followers=None, ig_followers=None):
        """Update channel record with follower counts"""
        try:
            print(f"      💾 Updating channel record: {channel_record_id}")
            
            # Build update fields
            update_fields = {}
            if youtube_followers is not None:
                update_fields['YouTube Followers'] = youtube_followers
                print(f"      📊 YouTube Followers: {youtube_followers:,}")
            
            if ig_followers is not None:
                update_fields['IG Followers'] = ig_followers
                print(f"      📊 IG Followers: {ig_followers:,}")
            
            if not update_fields:
                print(f"      ⚠️ No follower counts to update")
                return False
            
            record = {'fields': update_fields}
            
            url = f"{self.channels_table_url}/{channel_record_id}"
            response = requests.patch(url, 
                                    headers=self.airtable_headers, 
                                    json=record)
            
            print(f"      📡 Airtable update response status: {response.status_code}")
            
            if response.status_code == 200:
                print(f"      ✅ Successfully updated channel follower counts")
                return True
            else:
                error_data = response.json()
                print(f"      ❌ Airtable update failed: {error_data}")
                return False
                
        except Exception as e:
            print(f"      ❌ Error updating channel record: {e}")
            return False

    def process_single_channel_followers(self, channel_record):
        """Process follower counts for a single channel"""
        record_id = channel_record['id']
        fields = channel_record['fields']
        
        channel_name = fields.get('Social Media Account', 'Unknown')
        youtube_profile = fields.get('YouTube Profile', '')
        ig_profile = fields.get('IG Profile', '')
        
        print(f"🔍 Processing channel: {channel_name}")
        print(f"   YouTube: {youtube_profile}")
        print(f"   Instagram: {ig_profile}")
        print(f"   Record ID: {record_id}")
        
        youtube_followers = None
        ig_followers = None
        
        # Process YouTube if profile URL exists
        if youtube_profile:
            print(f"   🎥 Processing YouTube channel...")
            channel_id = self.extract_youtube_channel_id_from_url(youtube_profile)
            if channel_id:
                youtube_followers = self.get_youtube_subscriber_count(channel_id)
            else:
                print(f"   ⚠️ Could not extract YouTube channel ID from URL")
        
        # Process Instagram if profile URL exists
        if ig_profile:
            print(f"   📱 Processing Instagram account...")
            username = self.extract_instagram_username_from_url(ig_profile)
            if username:
                account_id = self.username_to_account_id.get(username)
                if account_id:
                    ig_followers = self.get_instagram_follower_count(account_id)
                else:
                    print(f"   ⚠️ Instagram account '{username}' not in mapping")
            else:
                print(f"   ⚠️ Could not extract Instagram username from URL")
        
        # Update channel record if we got any follower counts
        if youtube_followers is not None or ig_followers is not None:
            success = self.update_channel_follower_counts(record_id, youtube_followers, ig_followers)
            if success:
                summary = []
                if youtube_followers is not None:
                    summary.append(f"YouTube: {youtube_followers:,}")
                if ig_followers is not None:
                    summary.append(f"Instagram: {ig_followers:,}")
                print(f"   ✅ Updated: {', '.join(summary)}")
            return success
        else:
            print(f"   ⏭️ No follower counts retrieved - skipping update")
            return False

    def sync_follower_counts(self):
        """Main function to sync follower counts for all channels"""
        print("🚀 Starting Follower Count Sync...")
        print("📋 Fetching all channels from Airtable...")
        
        all_channels = self.get_all_channels_from_airtable()
        print(f"📊 Found {len(all_channels)} total channels")
        
        # Filter channels that have YouTube or Instagram profiles
        target_channels = []
        for channel in all_channels:
            fields = channel['fields']
            has_youtube = bool(fields.get('YouTube Profile', ''))
            has_instagram = bool(fields.get('IG Profile', ''))
            
            if has_youtube or has_instagram:
                target_channels.append(channel)
        
        print(f"🎯 Processing {len(target_channels)} channels with social profiles")
        
        # Process each channel
        success_count = 0
        skip_count = 0
        error_count = 0
        
        for i, channel in enumerate(target_channels, 1):
            print(f"\n📱 [{i}/{len(target_channels)}]", end=" ")
            
            try:
                success = self.process_single_channel_followers(channel)
                if success:
                    success_count += 1
                else:
                    skip_count += 1
                    
            except Exception as e:
                print(f"   ❌ Unexpected error: {e}")
                error_count += 1
            
            # Rate limiting
            time.sleep(1.0)  # Slightly longer delay for channel API calls
        
        # Summary
        print(f"\n🎉 Follower sync completed!")
        print(f"✅ Successfully updated: {success_count}")
        print(f"⏭️ Skipped (no profiles/issues): {skip_count}")
        print(f"❌ Unexpected errors: {error_count}")
        print(f"📊 Total processed: {success_count + skip_count + error_count}")
        
        if skip_count > 0:
            print(f"\n💡 To reduce skipped channels:")
            print(f"   • Add missing Instagram account mappings to username_to_account_id")
            print(f"   • Check YouTube profile URLs are valid")
            print(f"   • Verify Instagram profile URLs are correctly formatted")

def main():
    sync = PostsTableSync()
    
    # Choose what to run:
    
    # For post metrics sync:
    # sync.sync_all_posts()
    
    # For follower count sync:
    sync.sync_follower_counts()
    
    # For recent posts sync:
    # sync.sync_recent_posts(days_back=7)

if __name__ == "__main__":
    main()