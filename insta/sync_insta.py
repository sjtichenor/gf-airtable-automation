from insta_sync import InstagramDynamicSync

if __name__ == "__main__":
    sync = InstagramDynamicSync()
    sync.sync_instagram_posts()
    print("\n" + "="*60 + "\n")
    sync.sync_instagram_followers()