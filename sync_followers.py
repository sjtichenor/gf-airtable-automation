from sync_app import PostsTableSync

if __name__ == "__main__":
    sync = PostsTableSync()
    sync.sync_follower_counts()