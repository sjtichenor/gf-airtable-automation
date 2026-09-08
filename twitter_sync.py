import os
import json
import requests
import re
from datetime import datetime
from dotenv import load_dotenv
import time

# Load environment variables
load_dotenv()

class TwitterSync:
    def __init__(self):
        self.twitter_bearer_token = os.getenv('TWITTER_BEARER_TOKEN')
        self.airtable_token = os.getenv('AIRTABLE_PERSONAL_ACCESS_TOKEN')
        self.airtable_base_id = os.getenv('AIRTABLE_BASE_ID',"appxCYu0Tfwc6h7X7")
        self.posts_table_id = os.getenv('AIRTABLE_TABLE_ID', "tblMpYJQjbb5yuKfC")
        
        # API URLs
        self.twitter_api_url = "https://api.x.com/2/tweets"
        self.posts_table_url = f"https://api.airtable.com/v0/{self.airtable_base_id}/{self.posts_table_id}"
        
        # Headers
        self.airtable_headers = {
            'Authorization': f'Bearer {self.airtable_token}',
            'Content-Type': 'application/json'
        }
        
        self.twitter_headers = {
            'Authorization': f'Bearer {self.twitter_bearer_token}',
            'User-Agent': 'TwitterSync/1.0'
        }

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

    def extract_tweet_id_from_url(self, url):
        """Extract tweet ID from Twitter/X URL"""
        if not url:
            return None
        
        # Handle both twitter.com and x.com domains
        patterns = [
            r'(?:twitter\.com|x\.com)/[^/]+/status/(\d+)',
            r'(?:mobile\.twitter\.com|m\.twitter\.com)/[^/]+/status/(\d+)',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        
        return None

    def format_twitter_date(self, twitter_date_str):
        """Convert Twitter ISO date to Airtable date format"""
        try:
            # Twitter returns: "2025-01-15T14:30:00.000Z"
            # Parse the ISO format datetime
            dt = datetime.fromisoformat(twitter_date_str.replace('Z', '+00:00'))
            
            # Return in Airtable date format: "YYYY-MM-DD"
            return dt.strftime('%Y-%m-%d')
            
        except Exception as e:
            print(f"      ⚠️ Error formatting date {twitter_date_str}: {e}")
            return None

    def fetch_twitter_metrics_batch(self, tweet_data_list):
        """Fetch Twitter metrics for multiple tweets using proven API approach"""
        if not tweet_data_list:
            return {}
        
        print(f"📡 Fetching metrics for {len(tweet_data_list)} tweets...")
        
        # Group by batches of 100 (Twitter API limit)
        batch_size = 100
        all_metrics = {}
        
        for i in range(0, len(tweet_data_list), batch_size):
            batch_data = tweet_data_list[i:i + batch_size]
            batch_ids = [item['tweet_id'] for item in batch_data]
            
            print(f"   📦 Processing batch {i//batch_size + 1}: {len(batch_ids)} tweets")
            
            # Enhanced API structure - now includes created_at for Date Posted
            params = {
                "ids": ",".join(batch_ids),
                "tweet.fields": "public_metrics,created_at",  # Added created_at
                "expansions": "attachments.media_keys",
                "media.fields": "public_metrics"
            }
            
            try:
                response = requests.get(self.twitter_api_url, 
                                      headers=self.twitter_headers, 
                                      params=params)
                
                print(f"   📡 API Response Status: {response.status_code}")
                
                if response.status_code == 200:
                    data = response.json()
                    
                    # Create media lookup dictionary (for potential future use)
                    media_lookup = {}
                    if 'includes' in data and 'media' in data['includes']:
                        for media in data['includes']['media']:
                            media_lookup[media['media_key']] = media.get('public_metrics', {}).get('view_count', 0)
                    
                    # Process successful results
                    if 'data' in data:
                        print(f"   ✅ Successfully retrieved {len(data['data'])} tweet metrics")
                        
                        for tweet in data['data']:
                            tweet_id = tweet['id']
                            
                            # Extract public metrics using your proven structure
                            metrics = tweet.get('public_metrics', {})
                            likes = metrics.get('like_count', 0)
                            impressions = metrics.get('impression_count', 0)
                            
                            # Extract and format creation date
                            created_at = tweet.get('created_at')
                            formatted_date = None
                            if created_at:
                                formatted_date = self.format_twitter_date(created_at)
                            
                            # Store enhanced metrics for Airtable update
                            all_metrics[tweet_id] = {
                                'views': impressions,
                                'likes': likes,
                                'date_posted': formatted_date
                            }
                            
                            print(f"      🐦 Tweet {tweet_id}: {impressions:,} views, {likes} likes, posted {formatted_date}")
                    
                    # Handle errors for specific tweets
                    if 'errors' in data:
                        print(f"   ⚠️ {len(data['errors'])} tweets had errors:")
                        for error in data['errors']:
                            tweet_id = error.get('resource_id', 'unknown')
                            error_detail = error.get('detail', 'Unknown error')
                            print(f"      • Tweet {tweet_id}: {error_detail}")
                            
                            # Add zero metrics for failed tweets
                            all_metrics[tweet_id] = {
                                'views': 0,
                                'likes': 0,
                                'date_posted': None,
                                'error': error_detail
                            }
                
                else:
                    print(f"   ❌ Batch request failed: {response.status_code}")
                    if response.status_code == 429:
                        print(f"   🕐 Rate limit hit - will retry with delays")
                    print(f"   Response: {response.text}")
                    
                    # Add zero metrics for failed batch
                    for item in batch_data:
                        all_metrics[item['tweet_id']] = {
                            'views': 0,
                            'likes': 0,
                            'date_posted': None,
                            'error': f'API Error {response.status_code}'
                        }
            
            except Exception as e:
                print(f"   ❌ Request exception: {e}")
                for item in batch_data:
                    all_metrics[item['tweet_id']] = {
                        'views': 0,
                        'likes': 0,
                        'date_posted': None,
                        'error': str(e)
                    }
            
            # Rate limiting between batches (important for Twitter API)
            time.sleep(0.10)
        
        return all_metrics

    # def update_twitter_post_record(self, record_id, metrics):
    #     """Update Twitter post record with Views, Likes, and Date Posted"""
    #     try:
    #         print(f"      💾 Updating Airtable record: {record_id}")
            
    #         # Build update fields - include Date Posted if available
    #         update_fields = {
    #             'Views': metrics['views'],
    #             'Likes': metrics['likes']
    #         }
            
    #         # Add Date Posted if we have it
    #         if metrics.get('date_posted'):
    #             update_fields['Date Posted'] = metrics['date_posted']
    #             print(f"      📊 Twitter metrics: Views={metrics['views']:,}, Likes={metrics['likes']}, Date={metrics['date_posted']}")
    #         else:
    #             print(f"      📊 Twitter metrics: Views={metrics['views']:,}, Likes={metrics['likes']}, Date=Not available")
            
    #         record = {'fields': update_fields}
            
    #         url = f"{self.posts_table_url}/{record_id}"
    #         response = requests.patch(url, 
    #                                 headers=self.airtable_headers, 
    #                                 json=record)
            
    #         print(f"      📡 Airtable update response status: {response.status_code}")
            
    #         if response.status_code == 200:
    #             print(f"      ✅ Successfully updated Airtable record")
    #             return True
    #         else:
    #             error_data = response.json()
    #             print(f"      ❌ Airtable update failed: {error_data}")
    #             return False
                
    #     except Exception as e:
    #         print(f"      ❌ Error updating post record: {e}")
    #         return False

    def update_twitter_post_record(self, record_id, metrics, existing_date=None):
        """Update Twitter post record with Views, Likes, and Date Posted (if not already set)"""
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
                print(f"      📊 Twitter metrics: Views={metrics['views']:,}, Likes={metrics['likes']}, Date=kept existing ({existing_date})")
            elif api_date:
                # Add new date from API
                update_fields['Date Posted'] = api_date
                date_action = "added"
                print(f"      📊 Twitter metrics: Views={metrics['views']:,}, Likes={metrics['likes']}, Date=added ({api_date})")
            else:
                # No date available from API
                date_action = "unavailable"
                print(f"      📊 Twitter metrics: Views={metrics['views']:,}, Likes={metrics['likes']}, Date=not available from API")
            
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

    def safe_get_name(self, fields):
        """Safely extract name field, handling Airtable formula errors"""
        name = fields.get('Name', 'Unknown')
        
        # Handle Airtable formula errors
        if isinstance(name, dict) and 'error' in name:
            return f"[Formula Error: {name.get('error', 'Unknown')}]"
        
        # Handle None or other non-string values
        if not isinstance(name, str):
            return 'Unknown'
        
        return name

    def sync_twitter_posts(self):
        """Main function to sync Twitter post metrics"""
        print("🐦 Starting Twitter Posts Sync...")
        print("📋 Fetching all posts from Airtable...")
        
        all_posts = self.get_all_posts_from_airtable()
        print(f"📊 Found {len(all_posts)} total posts")
        
        # Filter for Twitter posts only and capture existing dates
        twitter_posts = []
        for post in all_posts:
            social_network = post['fields'].get('Social Network', '')
            post_url = post['fields'].get('Link to Post', '')
            
            if social_network == 'Twitter' and post_url:
                tweet_id = self.extract_tweet_id_from_url(post_url)
                if tweet_id:
                    # Capture existing Date Posted value
                    existing_date = post['fields'].get('Date Posted')
                    
                    twitter_posts.append({
                        'record_id': post['id'],
                        'name': self.safe_get_name(post['fields']),
                        'url': post_url,
                        'tweet_id': tweet_id,
                        'existing_date': existing_date
                    })
                else:
                    print(f"⚠️ Could not extract tweet ID from: {post_url}")
        
        print(f"🎯 Processing {len(twitter_posts)} Twitter posts")
        
        if not twitter_posts:
            print("❌ No valid Twitter posts found")
            return
        
        # Analyze existing dates
        posts_with_dates = sum(1 for post in twitter_posts if post.get('existing_date'))
        posts_without_dates = len(twitter_posts) - posts_with_dates
        
        print(f"📅 Date Analysis:")
        print(f"   Posts with existing dates: {posts_with_dates} (will be preserved)")
        print(f"   Posts without dates: {posts_without_dates} (will get API dates)")
        
        # Show sample of posts being processed
        print(f"\n📋 Sample Twitter posts to process:")
        for i, post in enumerate(twitter_posts[:5], 1):
            date_status = "has date" if post.get('existing_date') else "needs date"
            print(f"   {i}. {post['name'][:60]}... ({date_status})")
            print(f"      Tweet ID: {post['tweet_id']}")
            print(f"      URL: {post['url']}")
        if len(twitter_posts) > 5:
            print(f"   ... and {len(twitter_posts) - 5} more")
        
        # Fetch metrics in batches
        all_metrics = self.fetch_twitter_metrics_batch(twitter_posts)
        
        # Update each post
        success_count = 0
        skip_count = 0
        error_count = 0
        total_views = 0
        total_likes = 0
        dates_preserved = 0
        dates_added = 0
        dates_unavailable = 0
        
        print(f"\n📝 Updating Airtable records...")
        
        for i, post in enumerate(twitter_posts, 1):
            post_name = post.get('name', 'Unknown')
            existing_date = post.get('existing_date')
            
            print(f"\n📱 [{i}/{len(twitter_posts)}] {post_name[:50]}...")
            
            tweet_id = post['tweet_id']
            metrics = all_metrics.get(tweet_id, {})
            
            if 'error' in metrics:
                print(f"   ⚠️ Skipping due to error: {metrics['error']}")
                skip_count += 1
            elif metrics.get('views', 0) == 0 and metrics.get('likes', 0) == 0:
                print(f"   ⚠️ Skipping - no metrics retrieved (likely deleted/private tweet)")
                skip_count += 1
            else:
                try:
                    # success, date_action = self.update_twitter_post_record(
                    #     post['record_id'], 
                    #     metrics, 
                    #     existing_date
                    # )
                    result = self.update_twitter_post_record(
                        post['record_id'], 
                        metrics, 
                        existing_date
                    )

                    # Handle both return formats
                    if isinstance(result, tuple):
                        success, date_action = result
                    else:
                        success, date_action = result, "unknown"
                    
                    if success:
                        success_count += 1
                        total_views += metrics['views']
                        total_likes += metrics['likes']
                        
                        # Track date actions
                        if date_action == "preserved":
                            dates_preserved += 1
                        elif date_action == "added":
                            dates_added += 1
                        elif date_action == "unavailable":
                            dates_unavailable += 1
                        
                        # Customize success message based on date action
                        if date_action == "preserved":
                            print(f"   ✅ Updated: {metrics['views']:,} views, {metrics['likes']} likes (kept existing date)")
                        elif date_action == "added":
                            print(f"   ✅ Updated: {metrics['views']:,} views, {metrics['likes']} likes, added date: {metrics.get('date_posted')}")
                        else:
                            print(f"   ✅ Updated: {metrics['views']:,} views, {metrics['likes']} likes")
                    else:
                        error_count += 1
                except Exception as e:
                    print(f"   ❌ Unexpected error: {e}")
                    error_count += 1
            
            # Rate limiting between individual updates
            time.sleep(0.10)
        
        # Summary
        print(f"\n🎉 Twitter posts sync completed!")
        print(f"✅ Successfully updated: {success_count}")
        print(f"⏭️ Skipped (no metrics/errors): {skip_count}")
        print(f"❌ Update errors: {error_count}")
        print(f"📊 Total processed: {success_count + skip_count + error_count}")
        
        if success_count > 0:
            print(f"\n📈 Performance Summary:")
            print(f"👀 Total views (impressions): {total_views:,}")
            print(f"❤️  Total likes: {total_likes:,}")
            print(f"📊 Average views per post: {total_views // success_count:,}")
            print(f"📊 Average likes per post: {total_likes // success_count}")
        
        # Enhanced date summary
        print(f"\n📅 Date Posted Summary:")
        print(f"   📌 Existing dates preserved: {dates_preserved}")
        print(f"   ➕ New dates added from API: {dates_added}")
        print(f"   ❓ Dates unavailable from API: {dates_unavailable}")
        print(f"   📋 Manual date entry eliminated for {dates_added} posts")
        
        if dates_preserved > 0:
            print(f"   💡 Your manual dates were kept intact - no overwrites!")
        
        if skip_count > 0:
            print(f"\n💡 About skipped posts:")
            print(f"   • Deleted/private tweets show as 'no metrics'")
            print(f"   • Suspended accounts cannot be accessed")
            print(f"   • Invalid URLs are skipped during extraction")
        
        # API usage info
        batch_count = (len(twitter_posts) // 100) + 1
        print(f"\n📡 API Usage:")
        print(f"   • Twitter API requests made: {batch_count}")
        print(f"   • Rate limit: 300 requests per 15 minutes")
        print(f"   • Tweets per request: up to 100")
        print(f"   • Creation dates included at no extra cost")

def main():
    sync = TwitterSync()
    
    # Run Twitter posts sync
    sync.sync_twitter_posts()

if __name__ == "__main__":
    main()