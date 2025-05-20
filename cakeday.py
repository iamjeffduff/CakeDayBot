import sqlite3
import praw
from datetime import datetime, timezone, timedelta
import time
from google import genai  # Import the genai library
from pytz import timezone as pytz_timezone  # Rename pytz's timezone to avoid conflicts
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer  # Import sentiment analyzer
from config import CLIENT_ID, CLIENT_SECRET, USER_AGENT, REDDIT_USERNAME, REDDIT_PASSWORD, DATABASE_NAME, API_CALL_DELAY, GEMINI_API_KEY, GEMINI_MODELS  # Add GEMINI_MODELS
import prawcore
import random
from models import Database, SubredditManager, WishedUsersManager

# Initialize database and manager instances
db = Database(DATABASE_NAME)
subreddit_mgr = SubredditManager(db)
wished_users_mgr = WishedUsersManager(db)

# Global variable to track current Gemini model index
current_gemini_model_index = 0

def adapt_date(date_obj):
    return date_obj.isoformat()  # Convert date to ISO 8601 string

def convert_date(date_str):
    return datetime.strptime(date_str, "%Y-%m-%d").date()  # Convert ISO 8601 string back to date

sqlite3.register_adapter(datetime.date, adapt_date)
sqlite3.register_converter("DATE", convert_date)

def get_reddit_instance(max_retries=3, initial_delay=1):
    """
    Get a Reddit API client with retry logic for connection issues.

    Args:
        max_retries: Maximum number of retry attempts (default: 3)
        initial_delay: Initial delay between retries in seconds (default: 1)

    Returns:
        praw.Reddit: A configured Reddit API client
    """
    attempt = 0
    while attempt < max_retries:
        try:
            reddit = praw.Reddit(
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
                user_agent=USER_AGENT,
                username=REDDIT_USERNAME,
                password=REDDIT_PASSWORD
            )
            # Test the connection by accessing a property
            _ = reddit.user.me()
            return reddit

        except prawcore.OAuthException as e:
            print(f"    ❌ Reddit authentication error: Invalid credentials")
            raise  # Re-raise as this is a configuration issue that needs immediate attention

        except prawcore.ResponseException as e:
            if e.response.status_code == 429:  # Too Many Requests
                if attempt < max_retries - 1:
                    delay = initial_delay * (2 ** attempt) + random.uniform(0, 1)
                    print(f"    ⚠️ Reddit API rate limit exceeded. Retrying in {delay:.2f} seconds... (Attempt {attempt + 1}/{max_retries})")
                    time.sleep(delay)
                else:
                    print(f"    ❌ Reddit API rate limit exceeded after {max_retries} attempts")
                    raise
            else:
                print(f"    ❌ Reddit API error: {e.response.status_code} - {str(e)}")
                raise

        except (prawcore.ServerError, prawcore.RequestException) as e:
            if attempt < max_retries - 1:
                delay = initial_delay * (2 ** attempt) + random.uniform(0, 1)
                print(f"    ⚠️ Reddit API connection error. Retrying in {delay:.2f} seconds... (Attempt {attempt + 1}/{max_retries})")
                time.sleep(delay)
            else:
                print(f"    ❌ Failed to connect to Reddit API after {max_retries} attempts: {str(e)}")
                raise

        except Exception as e:
            print(f"    ❌ Unexpected error connecting to Reddit API: {str(e)}")
            raise

        attempt += 1

    raise Exception(f"Failed to initialize Reddit client after {max_retries} attempts")

def get_gemini_client(max_retries=3, initial_delay=1):
    """Get a Gemini API client with retry logic for connection issues."""
    global current_gemini_model_index
    attempt = 0
    
    while attempt < max_retries:
        try:
            client = genai.Client(api_key=GEMINI_API_KEY)
            
            # Get current model from the list
            if current_gemini_model_index >= len(GEMINI_MODELS):
                print("    ❌ All models exhausted")
                return None, None
                
            model_name = GEMINI_MODELS[current_gemini_model_index]
            
            # Test the connection
            response = client.models.generate_content(
                model=model_name,
                contents="test"
            )
            
            if response and hasattr(response, 'text'):
                return client, model_name
            
            print("    ❌ Error: Empty or invalid response from Gemini API")
            return None, None

        except Exception as e:
            error_code = getattr(e, 'code', None) 
            if error_code == 401:  # Unauthorized
                print(f"    ❌ Authentication error: Invalid API key")
                return None, None
                
            if error_code in (429, 503):  # Rate limit or Service unavailable
                current_gemini_model_index += 1
                print(f"    ⚠️ Service {error_code}, switching to model: {GEMINI_MODELS[current_gemini_model_index] if current_gemini_model_index < len(GEMINI_MODELS) else 'None'}")
                continue

            if attempt < max_retries - 1:
                delay = initial_delay * (2 ** attempt) + random.uniform(0, 1)
                print(f"    ⚠️ API error. Retrying in {delay:.2f}s... (Attempt {attempt + 1}/{max_retries})")
                time.sleep(delay)
            else:
                # After 3 attempts, try the next model
                current_gemini_model_index += 1
                if current_gemini_model_index < len(GEMINI_MODELS):
                    print(f"    ⚠️ Failed after {max_retries} attempts, switching to model: {GEMINI_MODELS[current_gemini_model_index]}")
                    attempt = 0  # Reset attempts for the new model
                    continue
                else:
                    print(f"    ❌ All models exhausted after retries")
                    return None, None

        attempt += 1
    
    return None, None

def post_cake_day_comment(reddit_obj, target_obj, gemini_message, max_retries=3, initial_delay=1):
    """
    Posts the generated Cake Day message as a comment with retry logic and specific error handling.

    Args:
        reddit_obj: The PRAW Reddit instance.
        target_obj: The PRAW object to reply to (either a Post or a Comment).
        gemini_message: The message generated by Gemini.
        max_retries: Maximum number of retry attempts (default: 3).
        initial_delay: Initial delay between retries in seconds (default: 1).

    Returns:
        bool: True if comment was posted successfully, False otherwise.
    """
    comment_text = f"{gemini_message}\n\n*I am a bot sending some cheer in a world that needs more. Run by /u/LordTSG*"
    attempt = 0
    
    while attempt < max_retries:
        try:
            # Attempt to post the comment
            comment = target_obj.reply(comment_text)
            print(f"    💬 Posted comment to {target_obj.author.name if target_obj.author else 'deleted user'}: {gemini_message}")
            print(f"    🔗 URL: http://reddit.com{target_obj.permalink}\n")
            return True

        except prawcore.exceptions.Forbidden as e:
            # Check if it's a rate limit error (403)
            if "RATELIMIT" in str(e).upper() and attempt < max_retries - 1:
                delay = initial_delay * (2 ** attempt) + random.uniform(0, 1)
                print(f"    ⚠️ Rate limit exceeded. Retrying in {delay:.2f} seconds... (Attempt {attempt + 1}/{max_retries})")
                time.sleep(delay)
            else:
                # Handle other Forbidden errors (e.g., banned from subreddit)
                print(f"    ❌ Forbidden error: Bot may be banned from this subreddit - {str(e)}")
                return False

        except prawcore.exceptions.ServerError as e:
            # Handle Reddit server errors
            if attempt < max_retries - 1:
                delay = initial_delay * (2 ** attempt) + random.uniform(0, 1)
                print(f"    ⚠️ Reddit server error. Retrying in {delay:.2f} seconds... (Attempt {attempt + 1}/{max_retries})")
                time.sleep(delay)
            else:
                print(f"    ❌ Reddit server error after {max_retries} attempts: {str(e)}")
                return False

        except prawcore.exceptions.RequestException as e:
            # Handle network-related errors
            if attempt < max_retries - 1:
                delay = initial_delay * (2 ** attempt) + random.uniform(0, 1)
                print(f"    ⚠️ Network error. Retrying in {delay:.2f} seconds... (Attempt {attempt + 1}/{max_retries})")
                time.sleep(delay)
            else:
                print(f"    ❌ Network error after {max_retries} attempts: {str(e)}")
                return False

        except Exception as e:
            # Handle any other unexpected errors
            print(f"    ❌ Unexpected error posting comment: {str(e)}")
            return False

        attempt += 1

    return False

def _get_title_context(context_type, post_title):
    if context_type == "comment":
        return f"The comment is in a post titled '{post_title}'. "
    else:
        return f"The post is titled '{post_title}'. "

def is_cake_day(reddit, username):
    try:
        if wished_users_mgr.has_been_wished(username):
            print(f"    ⏭️  Skipping {username}, already wished today.")
            return False

        redditor = reddit.redditor(username)
        if not hasattr(redditor, 'created_utc'):
            print(f"      ⚠️  Warning: Unable to retrieve 'created_utc' for user: {username}")
            return False

        account_creation_time = datetime.fromtimestamp(redditor.created_utc, timezone.utc)  # Use datetime's timezone.utc
        now_local = datetime.now(pytz_timezone('America/Toronto'))  # Use pytz's timezone for local time

        if (now_local - account_creation_time).days >= 365:  # Check if the account is at least 1 year old
            # Check if today is the exact anniversary of the account creation
            if (account_creation_time.month == now_local.month and
                account_creation_time.day == now_local.day):
                wished_users_mgr.mark_as_wished(username)
                return True

        return False
    except Exception as e:
        print(f"      ⚠️ Error checking Cake Day for user {username}: {e}")
        return False

def analyze_sentiment(text):
    """
    Analyze the sentiment of a given text using Vader SentimentIntensityAnalyzer.

    Args:
        text (str): The text to analyze.

    Returns:
        str: The overall sentiment ('positive', 'neutral', or 'negative').
    """
    analyzer = SentimentIntensityAnalyzer()
    sentiment_scores = analyzer.polarity_scores(text)
    if sentiment_scores['compound'] >= 0.05:
        return "positive"
    elif sentiment_scores['compound'] <= -0.05:
        return "negative"
    else:
        return "neutral"

def generate_cake_day_message(client, model_name, prompt):
    """Generate a cake day message using the Gemini API."""
    try:
        if not client or not model_name:
            return "Happy Cake Day! 🎂"
            
        print(f"    🤖 Generating message with model: {model_name}")
        response = client.models.generate_content(
            model=model_name,
            contents=prompt
        )
        
        if response and hasattr(response, 'text'):
            return response.text
        
    except Exception as e:
        print(f"    ⚠️ Error generating message: {str(e)}")
        
    return "Happy Cake Day! 🎂"

def process_item(reddit, item, item_type, subreddit_name, post_title=None, bot_performance=None):
    """
    Processes a Reddit item (Post or Comment) to check for Cake Day and post a message.

    Args:
        reddit: The PRAW Reddit instance.
        item: The Reddit item to process (either a Post or a Comment).
        item_type: A string indicating the item type ('post' or 'comment').
        subreddit_name: The name of the subreddit.
        post_title: The title of the post (only applicable for comments).
        bot_performance: Tuple containing (total_score, comment_count) for bot's performance.
    """
    if item.author and is_cake_day(reddit, item.author.name):
        account_creation_date = datetime.fromtimestamp(reddit.redditor(item.author.name).created_utc, timezone.utc)
        account_age_years = (datetime.now(timezone.utc) - account_creation_date).days // 365
        print(f"  🎉 Cake Day found for user: {item.author.name} (Account Age: {account_age_years} years) on their {item_type}:")

        item_score = f"{item.score:+}"  # Get the score of the current item with "+" or "-" prefix

        # Collect context for the Gemini prompt
        comment_chain_context = []
        try:
            if item_type == "comment":
                # Traverse the comment tree for relevant context
                parent = item.parent()
                parent_chain = []
                sibling_chain = []

                # Fetch up to 5 parent comments
                while parent and len(parent_chain) < 5:
                    parent_text = parent.body[:500] if hasattr(parent, "body") else (parent.selftext[:250] if hasattr(parent, "selftext") else "(no text content)")
                    parent_sentiment = analyze_sentiment(parent_text)
                    parent_chain.insert(0, {  # Insert at the beginning to maintain order
                        "author": parent.author.name if parent.author else "[deleted]",
                        "text": parent_text,
                        "type": "post" if isinstance(parent, praw.models.Submission) else "comment",
                        "post_hint": parent.post_hint if hasattr(parent, "post_hint") else None,
                        "sentiment": parent_sentiment,
                        "reddit_score": f"{parent.score:+}" if hasattr(parent, "score") else "+0",
                        "is_cake_day": parent.author.name == item.author.name if parent.author else False
                    })
                    parent = parent.parent() if hasattr(parent, "parent") else None

                # Add the parent chain to the context
                comment_chain_context.extend(parent_chain)

                # Add the current comment
                current_text = item.body[:500]
                current_sentiment = analyze_sentiment(current_text)
                comment_chain_context.append({
                    "author": item.author.name,
                    "text": current_text,
                    "type": "comment",
                    "post_hint": item.post_hint if hasattr(item, "post_hint") else None,
                    "sentiment": current_sentiment,
                    "reddit_score": f"{item.score:+}",
                    "is_cake_day": True
                })

                # Fetch up to 5 sibling comments
                siblings = item.parent().replies if hasattr(item.parent(), "replies") else []
                for sibling in siblings:
                    if sibling != item and len(sibling_chain) < 5:
                        sibling_text = sibling.body[:500] if hasattr(sibling, "body") else "(no text content)"
                        sibling_sentiment = analyze_sentiment(sibling_text)
                        sibling_chain.append({
                            "author": sibling.author.name if sibling.author else "[deleted]",
                            "text": sibling_text,
                            "type": "comment",
                            "post_hint": sibling.post_hint if hasattr(sibling, "post_hint") else None,
                            "sentiment": sibling_sentiment,
                            "reddit_score": f"{sibling.score:+}",
                            "is_cake_day": sibling.author.name == item.author.name if sibling.author else False
                        })

                # Add the sibling chain to the context
                comment_chain_context.extend(sibling_chain)

            elif item_type == "post":
                # Add the post's selftext as the first item in the context
                post_text = item.selftext[:500] if item.selftext else "(no text content)"
                post_sentiment = analyze_sentiment(post_text)
                comment_chain_context.append({
                    "author": item.author.name,
                    "text": post_text,
                    "type": "post",
                    "post_hint": item.post_hint if hasattr(item, "post_hint") else None,
                    "sentiment": post_sentiment,
                    "reddit_score": f"{item.score:+}",
                    "is_cake_day": True
                })

                # Fetch up to 10 top-level comments
                submission = item
                submission.comments.replace_more(limit=None)  # Load all top-level comments
                for comment in submission.comments[:10]:  # Limit to 10 comments
                    comment_text = comment.body[:500] if hasattr(comment, "body") else "(no text content)"
                    comment_sentiment = analyze_sentiment(comment_text)
                    comment_data = {
                        "author": comment.author.name if comment.author else "[deleted]",
                        "text": comment_text,
                        "type": "comment",
                        "post_hint": comment.post_hint if hasattr(comment, "post_hint") else None,
                        "sentiment": comment_sentiment,
                        "reddit_score": f"{comment.score:+}",
                        "is_cake_day": comment.author.name == item.author.name if comment.author else False
                    }

                    # Insert the Cake Day comment in its correct position
                    if comment.author and comment.author.name == item.author.name:
                        comment_data["is_cake_day"] = True
                    
                    comment_chain_context.append(comment_data) # Add the comment to the context
                    
        except Exception as e:
            print(f"    ⚠️ Error collecting comment chain context: {e}")

        # Calculate sentiment statistics
        sentiment_scores = [analyze_sentiment(entry["text"]) for entry in comment_chain_context]
        analyzer = SentimentIntensityAnalyzer()  # Create an instance of SentimentIntensityAnalyzer
        average_sentiment_score = sum([analyzer.polarity_scores(entry["text"])["compound"] for entry in comment_chain_context]) / len(comment_chain_context)
        most_extreme_sentiment = max(comment_chain_context, key=lambda x: abs(analyzer.polarity_scores(x["text"])["compound"]))
        sentiment_trend = "positive" if average_sentiment_score > 0 else "negative" if average_sentiment_score < 0 else "neutral"

        # Construct the Gemini prompt with bot performance data
        bot_total_score = bot_performance[0] if bot_performance else 0
        bot_comment_count = bot_performance[1] if bot_performance else 0
        bot_karma = (bot_total_score / bot_comment_count) if bot_comment_count > 0 else 0

        gemini_message_prompt = f"""
You are an AI-powered Reddit bot that celebrates users' Cake Days. Your goal is to craft a thoughtful and relevant message that fits naturally into the conversation, being transparent about your AI nature only when contextually appropriate (e.g., in AI-related subreddits or discussions). Keep responses concise and genuine. The focus should be on the cake day.

Context:
Subreddit: r/{subreddit_name}
Post Title: {post_title if post_title else item.title}
Post Type: {item.post_hint if hasattr(item, "post_hint") else "text"}
Conversation Summary: {comment_chain_context}

Sentiment Guide (Current conversation is {sentiment_trend}):
- Average Sentiment: {average_sentiment_score:.2f} (-1 to +1)
- Most Impactful Comment: "{most_extreme_sentiment["text"]}"

Content Status:
- {item_type.capitalize()} Karma: {item.score}
- Visibility: {'Low' if item.score < 1 else 'Normal' if item.score < 10 else 'High'}

About the User:
- Username: {item.author.name}
- Account Age: {account_age_years} year{"s" if account_age_years > 1 else ""}
- Activity Type: {'Post' if item_type == 'post' else 'Comment'}

Performance Metrics:
- Bot's Average Karma: {bot_karma:.2f}
- Previous Interactions: {bot_comment_count}
Message Guidelines Based on Performance:"""

        # Note: Remove the existing karma bands and replace with this more specific guidance
        if bot_karma < 0 or item.score < 1:
            gemini_message_prompt += f"""    
    IMPORTANT: Previous responses were not well-received in r/{subreddit_name}.
    - Study the conversation carefully before responding
    - Match the subreddit's general tone and style
    - Keep the message brief but relevant to the discussion
    - If in AI-related context, acknowledge it subtly if appropriate
    - If conversation is technical/serious, be professional
    - If conversation is casual, be natural but understated
    - Only use emojis if they appear in other comments
    - For AI/ML subreddits or discussions, show understanding but stay humble
    - Priority: Blend in > Celebrate"""
        elif bot_karma == 0 or bot_comment_count == 0:
            gemini_message_prompt += f"""
    IMPORTANT: First interaction in r/{subreddit_name}
    - Study the provided conversation context carefully
    - Keep message very brief (1 short sentence)
    - Make a meaningful contribution to the discussion
    - Match the tone and vocabulary of other comments
    - For technical/professional discussions, maintain formality
    - Avoid standing out as a bot
    - Only use emoji if present in other comments
    - Priority: Relevance > Celebration"""
        elif 0 < bot_karma < 3:
            gemini_message_prompt += f"""
    IMPORTANT: Slowly building acceptance in r/{subreddit_name}
    - Study the conversation context and identify the main topic/theme
    - Keep message concise (2 sentences maximum)
    - Make a meaningful contribution to the discussion
    - Match the community's vocabulary and terminology
    - If the conversation is lighthearted, match that energy
    - If the conversation is serious, maintain professionalism
    - Only use emoji if others are using them
    - For specialty subreddits, demonstrate topic understanding
    - Priority: Add Value > Celebrate > Stand Out"""
        else:
            gemini_message_prompt += f"""
    IMPORTANT: Strong acceptance in r/{subreddit_name}
    - Study the conversation context for key themes and terminology
    - Message length: 2-3 concise sentences
    - Make a meaningful contribution to the discussion
    - Include ONE of the following if relevant:
      * A brief, interesting fact related to the conversation
      * A thoughtful observation about the discussion
      * A gentle humorous reference (if tone appropriate)
    - Use subreddit-specific language/terms if present
    - Match the energy level of highly-upvoted comments
    - If technical subreddit, demonstrate subject knowledge
    - Emojis allowed if fitting the community style
    - Priority: Add Value = Celebrate > Entertain"""

        gemini_message_prompt += """

Response Requirements:
1. Match the tone of the conversation
2. Keep the cake day wish natural and understated
3. Avoid forced references to account age
4. Use Reddit formatting (bold, italics) sparingly
5. If sentiment is negative, be supportive rather than celebratory
6. Context-sensitive AI awareness:
   - If in AI/ML subreddits: Show understanding but stay humble
   - If post is about AI: Acknowledge the shared interest naturally
   - If post contains AI-generated content: Relate thoughtfully if appropriate
   - In non-AI contexts: Focus on the cake day and conversation topic
7. Never explicitly state "I am an AI" unless the context strongly warrants it

Your response should be only the cake day message, ready to post as a comment."""

        # Call Gemini API to generate the message
        print(f"""    
            Subreddit: r/{subreddit_name}
            Post Title: {post_title if post_title else item.title}
            Bot Karma: {bot_karma:.2f}
            Relevant Comment Chain:\n
              {comment_chain_context}\n
            Sentiment Analysis:
              - Average Sentiment Score: {average_sentiment_score:.2f} 
              - Most Extreme Sentiment: {most_extreme_sentiment["sentiment"]} (Text: "{most_extreme_sentiment["text"]}")
              - Sentiment Trend: {sentiment_trend}
        """)

        try:
            client, model_name = get_gemini_client()
            if client and model_name:
                print(f"    🤖 Using Gemini Model: {model_name}")
                gemini_message = generate_cake_day_message(client, model_name, gemini_message_prompt)
            else:
                print("    ⚠️ Failed to initialize Gemini client, using fallback message")
                gemini_message = "Happy Cake Day! 🎂"
        except Exception as e:
            print(f"    ⚠️ Failed to generate message using Gemini API: {str(e)}")
            gemini_message = "Happy Cake Day! 🎂"

        # Post the Cake Day wish
        post_cake_day_comment(reddit, item, gemini_message)
        return True  # Indicate that a Cake Day was found
    return False  # Indicate no Cake Day was found

def process_subreddit(reddit, subreddit_name, last_post_checked, bot_score):
    """
    Processes a subreddit looking for users celebrating their Cake Day.

    Args:
        reddit: The PRAW Reddit instance.
        subreddit_name: The name of the subreddit to process.
        last_post_checked: The ID of the last post that was checked.
        bot_score: Tuple containing (total_score, comment_count) for bot's performance in this subreddit.

    Returns:
        str: The ID of the newest post checked in this scan.
    """
    subreddit = reddit.subreddit(subreddit_name)
    new_last_post_checked = None  # Initialize as None
    cake_day_count = 0  # Counter for cake days found

    posts = subreddit.new(limit=25)  # Check the 25 newest posts (adjust as needed)

    for post in posts:
        if not new_last_post_checked:
            new_last_post_checked = post.id  # Set to the first post checked

        if last_post_checked and post.id == last_post_checked:
            print(f"    ⚠️ Reached the last checked post: {post.title}. Stopping scan.")
            break  # We've reached the last checked post

        if post.author:
            print(f"  Checking post: '{post.title}' by {post.author.name} with {post.num_comments} comments. Please stand by...")
            if process_item(reddit, post, "post", subreddit_name, bot_performance=bot_score):  # Use bot_score passed from main loop
                cake_day_count += 1  # Increment only if a Cake Day was found
            time.sleep(API_CALL_DELAY)  # Be mindful of rate limits

        post.comments.replace_more(limit=None)  # Load all top-level comments
        for comment in post.comments.list():
            if comment.author:
                if process_item(reddit, comment, "comment", subreddit_name, post.title, bot_performance=bot_score):  # Use bot_score passed from main loop
                    cake_day_count += 1  # Increment only if a Cake Day was found
                time.sleep(API_CALL_DELAY)

    print(f"\n🎉 Total Cake Days found in r/{subreddit_name}: {cake_day_count} {"" if cake_day_count == 0 else "🎉🎉"}")
    return new_last_post_checked

def get_bot_comment_score(reddit, subreddit_name, days_to_check=30):
    """
    Calculate the overall score of the bot's comments in a subreddit.

    Args:
        reddit: The PRAW Reddit instance.
        subreddit_name: The name of the subreddit.
        days_to_check: Number of days to look back for comments (default: 30)

    Returns:
        tuple: (total_score, comment_count)
    """
    try:
        bot_user = reddit.redditor(REDDIT_USERNAME)
        total_score = 0
        comment_count = 0
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=days_to_check)

        for comment in bot_user.comments.new(limit=100):
            created_date = datetime.fromtimestamp(comment.created_utc, timezone.utc)
            
            if (comment.subreddit.display_name.lower() == subreddit_name.lower() and 
                created_date > cutoff_date):
                total_score += comment.score
                comment_count += 1

        print(f"\n📈 Summary for r/{subreddit_name}:")
        print(f"  - Total comments found: {comment_count}")
        print(f"  - Total score: {total_score:+}")
        print(f"  - Average score per comment: {(total_score/comment_count if comment_count else 0):+.2f}\n")
        
        return total_score, comment_count
    except Exception as e:
        print(f"⚠️ Error fetching bot comments for r/{subreddit_name}: {e}")
        return 0, 0

if __name__ == "__main__":
    # Initialize Reddit instance
    reddit = get_reddit_instance()
    
    wished_users_mgr.clear_expired()
    subreddit_info = subreddit_mgr.get_info()
    
    for subreddit_name, (last_post_checked, last_scan_time) in subreddit_info.items():
        print(f"\n🔍 Processing r/{subreddit_name}")
        bot_score = get_bot_comment_score(reddit, subreddit_name)
        new_last_post_checked = process_subreddit(reddit, subreddit_name, last_post_checked, bot_score)
        subreddit_mgr.update_last_post_checked(subreddit_name, new_last_post_checked)
        subreddit_mgr.update_scan_time(subreddit_name)
