# CakeDayBot 🎂

CakeDayBot is a Reddit bot designed to celebrate users' Cake Days by posting personalized and context-aware messages. It uses the Reddit API (via PRAW), sentiment analysis (via VaderSentiment), and the Gemini AI API to craft thoughtful messages based on the surrounding conversation.

## Features
- Detects users' Cake Days based on their account creation date.
- Analyzes the sentiment of the surrounding conversation to guide the tone of the message.
- Posts personalized Cake Day wishes as comments on Reddit.
- Tracks users who have already been wished to avoid duplicate messages.
- Supports collaboration through suggestions for improvement.

## Detailed Bot Functionality

### 1. Image Processing
- Intelligent image handling with automatic resizing limits
- RGB conversion optimization for better performance
- Smart image caching system to reduce API calls
- Context-aware image processing for Gemini AI prompts
- Support for multiple image types (direct, preview, gallery)

### 2. Sentiment Analysis
- Cached sentiment analysis to improve response time
- Global sentiment cache with automatic cleanup
- Intelligent cache TTL management
- Thread-safe sentiment analysis operations

### 3. Comment Management
- Optimized comment loading with limit=0 for MoreComments
- Smart filtering of top-level comments (limited to 50)
- Efficient comment tree traversal
- Cached comment data for improved performance

### 4. Performance Metrics
- Persistent bot performance caching
- Real-time performance monitoring
- Automated cache cleanup with TTL
- Performance metrics tracking and analysis

## Changelog

### v1.1.0 (Latest)
#### Added
- Image processing optimizations:
  - Automatic image resizing
  - RGB conversion optimization
  - Smart image caching
  - Enhanced image context for Gemini
- Sentiment analysis caching system
- Comment loading optimizations
- Bot performance metrics caching

#### Changed
- Improved comment tree traversal efficiency
- Enhanced image handling logic
- Updated cache management system

#### Fixed
- Memory usage in image processing
- Performance issues in comment loading
- Cache cleanup efficiency

### v1.0.0 (Initial Release)
- Basic Cake Day detection and celebration
- Simple sentiment analysis
- Reddit API integration
- Initial database schema
- Basic user tracking

## Ownership and Contributions
CakeDayBot is owned and maintained solely by LordTSG. While contributions and suggestions are welcome, all changes must be approved by the owner to ensure the integrity and consistency of the bot. Forks and derivative bots are discouraged to maintain CakeDayBot as the only official Cake Day bot.

## Requirements
- Python 3.8 or higher
- A Reddit account with API credentials
- A Gemini AI API key
- SQLite for local data storage

## Setup

### 1. Clone the Repository
```bash
git clone https://github.com/iamjeffduff/CakeDayBot.git
cd CakeDayBot
```

### 2. Install Dependencies
Create a virtual environment and install the required Python packages:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Configure the Bot
Create a `config.py` file in the project directory and add your credentials:
```python
# config.py
CLIENT_ID = "your_reddit_client_id"
CLIENT_SECRET = "your_reddit_client_secret"
USER_AGENT = "your_user_agent"
REDDIT_USERNAME = "your_reddit_username"
REDDIT_PASSWORD = "your_reddit_password"
DATABASE_NAME = "subreddits.db"
API_CALL_DELAY = 1
GEMINI_API_KEY = "your_gemini_api_key"
```

Add `config.py` to `.gitignore` to prevent it from being tracked:
```plaintext
config.py
```

### 4. Initialize the Database
Run the following command to create the SQLite database:
```bash
python -c "import sqlite3; conn = sqlite3.connect('subreddits.db'); conn.execute('CREATE TABLE IF NOT EXISTS wished_users (username TEXT PRIMARY KEY, wished_date DATE); conn.execute('CREATE TABLE IF NOT EXISTS subreddits (subreddit_name TEXT PRIMARY KEY, last_post_checked TEXT, last_scan_time REAL); conn.close()')"
```

### 5. Run the Bot
Start the bot:
```bash
python cakeday.py
```

## Usage
- The bot scans subreddits listed in the database for new posts and comments.
- It identifies users celebrating their Cake Day and posts a personalized message.
- Sentiment analysis ensures the tone of the message matches the surrounding conversation.

## Contributing
Contributions are welcome as suggestions to improve the original CakeDayBot. To propose changes:
1. Fork the repository.
2. Create a new branch for your feature or bug fix:
   ```bash
   git checkout -b feature-name
   ```
3. Commit your changes:
   ```bash
   git commit -m "Add feature-name"
   ```
4. Push to your fork:
   ```bash
   git push origin feature-name
   ```
5. Open a pull request with a detailed explanation of your changes.

All contributions will be reviewed, and only approved changes will be merged into the main bot.

## License
This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.

## Acknowledgments
- [PRAW](https://praw.readthedocs.io/) for Reddit API integration.
- [VaderSentiment](https://github.com/cjhutto/vaderSentiment) for sentiment analysis.
- [Gemini AI](https://www.google.com/) for generating personalized messages.

## Disclaimer
This bot is for educational purposes only. Use responsibly and ensure compliance with Reddit's API terms of service.
