import os
import json
import requests
import re
from datetime import datetime
from dotenv import load_dotenv
import time

# Load environment variables
load_dotenv()

class YouTubeSync:
    def __init__(self):
        self.youtube_api_key = os.getenv('YOUTUBE_API_KEY')
        self.airtable_token = os.getenv('AIRTABLE_PERSONAL_ACCESS_TOKEN')
        self.airtable_base_id = os.getenv('AIRTABLE_BASE_ID')
        self.posts_table_id = os.getenv('AIRTABLE_TABLE_ID', "tblMpYJQjbb5yuKfC")
        self.channels_table_id = os.getenv('AIRTABLE_CHANNELS_TABLE_ID', "tblP8bh3crTHLVoZA")
        
        # API base URLs
        self.youtube_base_url = os.getenv('YOUTUBE_API_BASE_URL', "https://www.googleapis.com/youtube/v3")
        
        # Airtable URLs
        self.posts_table_url = f"https://api.airtable.com/v0/{self.airtable_base_id}/{self.posts_table_id}"
        self.channels_table_url = f"https://api.airtable.com/v0/{self.airtable_base_id}/{self.channels_table_id}"
        
        # Headers
        self.airtable_headers = {
            'Authorization': f'Bearer {self.airtable_token}',
            'Content-Type': 'application/json'
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
                print(f"Warning: Error fetching posts: {data}")
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
                print(f"Warning: Error fetching channels: {data}")
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
            print(f"Warning: Error fetching channel {channel_id}: {response.json()}")
            return None

    def extract_youtube_video_id(self, post_url):
        """Extract YouTube video ID from URL"""
        if not post_url:
            return None
            
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
                return match.group(1)
        
        return None

    # def get_youtube_video_metrics(self, video_id):
    #     """Get YouTube video metrics"""
    #     try:
    #         print(f"      Fetching YouTube metrics for video: {video_id}")
    #         url = f"{self.youtube_base_url}/videos"
    #         params = {
    #             'key': self.youtube_api_key,
    #             'id': video_id,
    #             'part': 'statistics'
    #         }
            
    #         response = requests.get(url, params=params)
    #         print(f"      YouTube API response status: {response.status_code}")
    #         data = response.json()
            
    #         if 'items' not in data or len(data['items']) == 0:
    #             print(f"      Warning: YouTube video not found: {video_id}")
    #             return None
            
    #         stats = data['items'][0]['statistics']
    #         metrics = {
    #             'views': int(stats.get('viewCount', 0)),
    #             'likes': int(stats.get('likeCount', 0))
    #         }
    #         print(f"      Success: YouTube metrics retrieved: {metrics}")
    #         return metrics
            
    #     except Exception as e:
    #         print(f"      Error fetching YouTube metrics for {video_id}: {e}")
    #         return None

    def get_youtube_video_metrics(self, video_id):
        """Get YouTube video metrics and published date"""
        try:
            print(f"      Fetching YouTube metrics for video: {video_id}")
            url = f"{self.youtube_base_url}/videos"
            params = {
                'key': self.youtube_api_key,
                'id': video_id,
                'part': 'statistics,snippet'  # Added snippet for published date
            }
            
            response = requests.get(url, params=params)
            print(f"      YouTube API response status: {response.status_code}")
            data = response.json()
            
            if 'items' not in data or len(data['items']) == 0:
                print(f"      Warning: YouTube video not found: {video_id}")
                return None
            
            stats = data['items'][0]['statistics']
            snippet = data['items'][0]['snippet']
            
            # Parse the published date
            published_at = snippet.get('publishedAt')
            date_posted = None
            if published_at:
                # Convert ISO format to YYYY-MM-DD for Airtable
                dt = datetime.fromisoformat(published_at.replace('Z', '+00:00'))
                date_posted = dt.strftime('%Y-%m-%d')
            
            metrics = {
                'views': int(stats.get('viewCount', 0)),
                'likes': int(stats.get('likeCount', 0)),
                'date_posted': date_posted
            }
            print(f"      Success: YouTube metrics retrieved: Views={metrics['views']}, Likes={metrics['likes']}, Date={metrics['date_posted']}")
            return metrics
            
        except Exception as e:
            print(f"      Error fetching YouTube metrics for {video_id}: {e}")
            return None
    
    # def update_post_record(self, record_id, metrics):
    #     """Update YouTube post record with Views and Likes"""
    #     try:
    #         print(f"      Updating Airtable record: {record_id}")
            
    #         record = {
    #             'fields': {
    #                 'Views': metrics['views'],
    #                 'Likes': metrics['likes']
    #             }
    #         }
            
    #         print(f"      YouTube metrics: Views={metrics['views']}, Likes={metrics['likes']}")
            
    #         url = f"{self.posts_table_url}/{record_id}"
    #         response = requests.patch(url, 
    #                                 headers=self.airtable_headers, 
    #                                 json=record)
            
    #         print(f"      Airtable update response status: {response.status_code}")
            
    #         if response.status_code == 200:
    #             print(f"      Success: Updated Airtable record")
    #             return True
    #         else:
    #             error_data = response.json()
    #             print(f"      Error: Airtable update failed: {error_data}")
    #             return False
                
    #     except Exception as e:
    #         print(f"      Error updating post record: {e}")
    #         return False

    def update_post_record(self, record_id, metrics, existing_date_posted=None):
        """Update YouTube post record with Views, Likes, and Date Posted (if not already set)"""
        try:
            print(f"      Updating Airtable record: {record_id}")
            
            # Build the fields to update
            fields_to_update = {
                'Views': metrics['views'],
                'Likes': metrics['likes']
            }
            
            # Only add Date Posted if we have it AND it's not already set
            if metrics.get('date_posted') and not existing_date_posted:
                fields_to_update['Date Posted'] = metrics['date_posted']
                print(f"      Adding Date Posted: {metrics['date_posted']} (field was empty)")
            elif existing_date_posted:
                print(f"      Keeping existing Date Posted: {existing_date_posted} (not overwriting)")
            
            record = {
                'fields': fields_to_update
            }
            
            date_status = f"Date Posted={metrics.get('date_posted', 'N/A')}" if not existing_date_posted else f"Date Posted=kept existing ({existing_date_posted})"
            print(f"      YouTube metrics: Views={metrics['views']}, Likes={metrics['likes']}, {date_status}")
            
            url = f"{self.posts_table_url}/{record_id}"
            response = requests.patch(url, 
                                    headers=self.airtable_headers, 
                                    json=record)
            
            print(f"      Airtable update response status: {response.status_code}")
            
            if response.status_code == 200:
                print(f"      Success: Updated Airtable record")
                return True
            else:
                error_data = response.json()
                print(f"      Error: Airtable update failed: {error_data}")
                return False
                
        except Exception as e:
            print(f"      Error updating post record: {e}")
            return False

    def process_single_post(self, post_record):
        """Process a single YouTube post record"""
        record_id = post_record['id']
        fields = post_record['fields']
        
        # Get post details
        post_name = fields.get('Name', 'Unknown')
        post_url = fields.get('Link to Post', '')
        social_network = fields.get('Social Network', '')
        
        # Handle formula errors in Name field
        if isinstance(post_name, dict) and 'error' in post_name:
            post_name = f"[Formula Error: {post_name.get('error', 'Unknown')}]"
        
        print(f"Processing: {str(post_name)[:60]}...")
        print(f"   Platform: {social_network} | URL: {post_url}")
        print(f"   Record ID: {record_id}")
        
        # Skip if not YouTube
        if social_network != 'YouTube':
            print(f"   Skipping {social_network} (not YouTube)")
            return False
        
        # Extract video ID from URL
        video_id = self.extract_youtube_video_id(post_url)
        
        if not video_id:
            print(f"   Warning: Could not extract video ID from URL")
            return False
        
        print(f"   Success: Extracted video ID: {video_id}")
        
        # Get metrics
        print(f"   Processing YouTube video...")
        metrics = self.get_youtube_video_metrics(video_id)
        
        # Update record if we got metrics
        if metrics:
            print(f"   Metrics retrieved successfully")
            # success = self.update_post_record(record_id, metrics)
            # Check if Date Posted already exists
            existing_date_posted = fields.get('Date Posted', None)
            success = self.update_post_record(record_id, metrics, existing_date_posted)
            if success:
                date_info = f", Date Posted: {metrics.get('date_posted', 'N/A')}" if metrics.get('date_posted') else ""
                print(f"   Success: Updated {metrics['views']} views, {metrics['likes']} likes{date_info}")
                # print(f"   Success: Updated {metrics['views']} views, {metrics['likes']} likes")
                return True
            else:
                print(f"   Error: Failed to update Airtable record")
                return False
        else:
            print(f"   Warning: Could not fetch metrics")
            return False

    def sync_youtube_posts(self):
        """Sync YouTube post metrics"""
        print("Starting YouTube Posts Sync...")
        print("Fetching all posts from Airtable...")
        
        all_posts = self.get_all_posts_from_airtable()
        print(f"Found {len(all_posts)} total posts")
        
        # Filter for YouTube posts only
        youtube_posts = []
        for post in all_posts:
            social_network = post['fields'].get('Social Network', '')
            if social_network == 'YouTube':
                youtube_posts.append(post)
        
        print(f"Processing {len(youtube_posts)} YouTube posts")
        
        if not youtube_posts:
            print("No YouTube posts found")
            return {'success': 0, 'skip': 0, 'error': 0}
        
        # Process each post
        success_count = 0
        skip_count = 0
        error_count = 0
        
        for i, post in enumerate(youtube_posts, 1):
            print(f"\n[{i}/{len(youtube_posts)}]", end=" ")
            
            try:
                success = self.process_single_post(post)
                if success:
                    success_count += 1
                else:
                    skip_count += 1
                    
            except Exception as e:
                print(f"   Unexpected error: {e}")
                error_count += 1
            
            # Rate limiting
            time.sleep(0.10)
        
        # Summary
        print(f"\nYouTube posts sync completed!")
        print(f"Successfully updated: {success_count}")
        print(f"Skipped (no mapping/issues): {skip_count}")
        print(f"Unexpected errors: {error_count}")
        print(f"Total processed: {success_count + skip_count + error_count}")
        
        return {
            'success': success_count,
            'skip': skip_count,
            'error': error_count
        }

    # YOUTUBE FOLLOWER SYNC METHODS

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
            print(f"      Resolving YouTube channel name: {channel_name}")
            
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
            print(f"      YouTube search API response status: {response.status_code}")
            data = response.json()
            
            if 'items' in data and len(data['items']) > 0:
                channel_id = data['items'][0]['snippet']['channelId']
                print(f"      Success: Resolved channel ID: {channel_id}")
                return channel_id
            else:
                print(f"      Warning: Could not resolve channel name: {channel_name}")
                return None
                
        except Exception as e:
            print(f"      Error resolving channel name {channel_name}: {e}")
            return None

    def get_youtube_subscriber_count(self, channel_id):
        """Get YouTube channel subscriber count"""
        try:
            print(f"      Fetching YouTube subscriber count for channel: {channel_id}")
            url = f"{self.youtube_base_url}/channels"
            params = {
                'key': self.youtube_api_key,
                'id': channel_id,
                'part': 'statistics'
            }
            
            response = requests.get(url, params=params)
            print(f"      YouTube channels API response status: {response.status_code}")
            data = response.json()
            
            if 'items' not in data or len(data['items']) == 0:
                print(f"      Warning: YouTube channel not found: {channel_id}")
                return None
            
            stats = data['items'][0]['statistics']
            # Note: subscriberCount might be hidden by channel owner
            subscriber_count = stats.get('subscriberCount')
            
            if subscriber_count is None:
                print(f"      Warning: Subscriber count is hidden for channel: {channel_id}")
                return None
            
            subscriber_count = int(subscriber_count)
            print(f"      Success: YouTube subscriber count: {subscriber_count:,}")
            return subscriber_count
            
        except Exception as e:
            print(f"      Error fetching YouTube subscriber count for {channel_id}: {e}")
            return None

    def update_channel_youtube_followers(self, channel_record_id, subscribers_count):
        """Update channel record with YouTube followers count"""
        try:
            print(f"      Updating channel record: {channel_record_id}")
            
            record = {
                'fields': {
                    'YouTube Followers': subscribers_count
                }
            }
            
            print(f"      YouTube Followers: {subscribers_count:,}")
            
            url = f"{self.channels_table_url}/{channel_record_id}"
            response = requests.patch(url, 
                                    headers=self.airtable_headers, 
                                    json=record)
            
            print(f"      Airtable update response status: {response.status_code}")
            
            if response.status_code == 200:
                print(f"      Success: Updated channel followers")
                return True
            else:
                error_data = response.json()
                print(f"      Error: Airtable update failed: {error_data}")
                return False
                
        except Exception as e:
            print(f"      Error updating channel record: {e}")
            return False

    def process_single_channel_followers(self, channel_record):
        """Process follower counts for a single channel"""
        record_id = channel_record['id']
        fields = channel_record['fields']
        
        channel_name = fields.get('Channel', 'Unknown')
        youtube_profile = fields.get('YouTube Profile', '')
        
        print(f"Processing channel: {channel_name}")
        print(f"   YouTube: {youtube_profile}")
        print(f"   Record ID: {record_id}")
        
        if not youtube_profile:
            print(f"   No YouTube profile found - skipping")
            return False
        
        print(f"   Processing YouTube channel...")
        channel_id = self.extract_youtube_channel_id_from_url(youtube_profile)
        
        if not channel_id:
            print(f"   Warning: Could not extract YouTube channel ID from URL")
            return False
        
        subscriber_count = self.get_youtube_subscriber_count(channel_id)
        
        if subscriber_count is not None:
            success = self.update_channel_youtube_followers(record_id, subscriber_count)
            if success:
                print(f"   Success: Updated YouTube subscribers: {subscriber_count:,}")
                return True
            else:
                print(f"   Error: Failed to update channel record")
                return False
        else:
            print(f"   Warning: Could not get subscriber count")
            return False

    def sync_youtube_followers(self):
        """Sync YouTube follower counts for all channels"""
        print("Starting YouTube Followers Sync...")
        print("Fetching all channels from Airtable...")
        
        all_channels = self.get_all_channels_from_airtable()
        print(f"Found {len(all_channels)} total channels")
        
        # Filter channels that have YouTube profiles
        youtube_channels = []
        for channel in all_channels:
            fields = channel['fields']
            has_youtube = bool(fields.get('YouTube Profile', ''))
            
            if has_youtube:
                youtube_channels.append(channel)
        
        print(f"Processing {len(youtube_channels)} channels with YouTube profiles")
        
        if not youtube_channels:
            print("No channels with YouTube profiles found")
            return {'success': 0, 'skip': 0, 'error': 0}
        
        # Process each channel
        success_count = 0
        skip_count = 0
        error_count = 0
        
        for i, channel in enumerate(youtube_channels, 1):
            print(f"\n[{i}/{len(youtube_channels)}]", end=" ")
            
            try:
                success = self.process_single_channel_followers(channel)
                if success:
                    success_count += 1
                else:
                    skip_count += 1
                    
            except Exception as e:
                print(f"   Unexpected error: {e}")
                error_count += 1
            
            # Rate limiting
            time.sleep(0.10)
        
        # Summary
        print(f"\nYouTube followers sync completed!")
        print(f"Successfully updated: {success_count}")
        print(f"Skipped (no profiles/issues): {skip_count}")
        print(f"Unexpected errors: {error_count}")
        print(f"Total processed: {success_count + skip_count + error_count}")
        
        return {
            'success': success_count,
            'skip': skip_count,
            'error': error_count
        }

    def sync_all_youtube(self):
        """Main function to sync both YouTube posts and followers"""
        print("="*80)
        print("STARTING COMPLETE YOUTUBE SYNC")
        print("="*80)
        
        # Sync posts first
        posts_results = self.sync_youtube_posts()
        
        print("\n" + "="*60 + "\n")
        
        # Sync followers second
        followers_results = self.sync_youtube_followers()
        
        # Combined summary
        print("\n" + "="*80)
        print("COMPLETE YOUTUBE SYNC SUMMARY")
        print("="*80)
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
        print("="*80)

def main():
    sync = YouTubeSync()
    
    # Run complete YouTube sync (posts + followers)
    sync.sync_all_youtube()

if __name__ == "__main__":
    main()