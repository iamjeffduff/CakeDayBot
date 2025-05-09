import sqlite3
import praw
from datetime import datetime, timezone, timedelta
import time
from google import genai  # Import the genai library
from pytz import timezone as pytz_timezone  # Rename pytz's timezone to avoid conflicts
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer  # Import sentiment analyzer
from config import CLIENT_ID, CLIENT_SECRET, USER_AGENT, REDDIT_USERNAME, REDDIT_PASSWORD, DATABASE_NAME, API_CALL_DELAY, GEMINI_API_KEY  # Import global variables
import prawcore
import random

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
    """
    Get a Gemini API client with retry logic for connection issues.

    Args:
        max_retries: Maximum number of retry attempts (default: 3)
        initial_delay: Initial delay between retries in seconds (default: 1)

    Returns:
        genai.Client: A configured Gemini API client
    """
    attempt = 0
    while attempt < max_retries:
        try:
            client = genai.Client(api_key=GEMINI_API_KEY)
            # Test the client with a simple request
            client.models.list()
            return client

        except genai.AuthenticationError as e:
            print(f"    ❌ Gemini API authentication error: Invalid API key")
            raise  # Re-raise as this is a configuration issue that needs immediate attention

        except genai.QuotaExceededError as e:
            print(f"    ❌ Gemini API quota exceeded: {str(e)}")
            raise  # Re-raise as retrying won't help with quota issues

        except (genai.ServiceUnavailableError, genai.ConnectionError) as e:
            if attempt < max_retries - 1:
                delay = initial_delay * (2 ** attempt) + random.uniform(0, 1)
                print(f"    ⚠️ Gemini API connection error. Retrying in {delay:.2f} seconds... (Attempt {attempt + 1}/{max_retries})")
                time.sleep(delay)
            else:
                print(f"    ❌ Failed to connect to Gemini API after {max_retries} attempts: {str(e)}")
                raise

        except Exception as e:
            print(f"    ❌ Unexpected error connecting to Gemini API: {str(e)}")
            raise

        attempt += 1

    raise Exception(f"Failed to initialize Gemini client after {max_retries} attempts")

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

def execute_db_operation(operation, params=None, max_retries=3, initial_delay=1):
    """
    Execute a database operation with retry logic.
    
    Args:
        operation: SQL query to execute
        params: Parameters for the SQL query
        max_retries: Maximum number of retry attempts
        initial_delay: Initial delay between retries in seconds
        
    Returns:
        tuple: (success, result) where success is a boolean and result is the query result or None
    """
    attempt = 0
    while attempt < max_retries:
        try:
            conn = sqlite3.connect(DATABASE_NAME, detect_types=sqlite3.PARSE_DECLTYPES, timeout=20)
            cursor = conn.cursor()
            
            if params:
                cursor.execute(operation, params)
            else:
                cursor.execute(operation)
                
            result = cursor.fetchall() if cursor.description else None
            conn.commit()
            conn.close()
            return True, result
            
        except sqlite3.OperationalError as e:
            if "database is locked" in str(e) and attempt < max_retries - 1:
                delay = initial_delay * (2 ** attempt) + random.uniform(0, 1)
                print(f"    ⚠️ Database is locked. Retrying in {delay:.2f} seconds... (Attempt {attempt + 1}/{max_retries})")
                time.sleep(delay)
            else:
                print(f"    ❌ Database error: {str(e)}")
                return False, None
                
        except sqlite3.IntegrityError as e:
            print(f"    ❌ Database integrity error: {str(e)}")
            return False, None
            
        except Exception as e:
            print(f"    ❌ Unexpected database error: {str(e)}")
            return False, None
            
        finally:
            if 'conn' in locals():
                try:
                    conn.close()
                except:
                    pass
                    
        attempt += 1
    
    return False, None

def mark_as_wished(username):
    today = datetime.now().date().isoformat()
    success, _ = execute_db_operation(
        "INSERT OR REPLACE INTO wished_users (username, wished_date) VALUES (?, ?)",
        (username, today)
    )
    return success

def has_been_wished(username):
    today = datetime.now().date()
    success, result = execute_db_operation(
        "SELECT wished_date FROM wished_users WHERE username = ?",
        (username,)
    )
    
    if not success or not result:
        return False
        
    wished_date = result[0][0]
    if isinstance(wished_date, str):
        wished_date = datetime.strptime(wished_date, "%Y-%m-%d").date()
        
    if wished_date == today:
        return True
    else:
        execute_db_operation(
            "DELETE FROM wished_users WHERE username = ?",
            (username,)
        )
        return False

def clear_expired_wished_users():
    today = datetime.now().date().isoformat()
    success, _ = execute_db_operation(
        "DELETE FROM wished_users WHERE wished_date < ?",
        (today,)
    )
    return success

def get_subreddit_info_from_database():
    success, result = execute_db_operation(
        "SELECT subreddit_name, last_post_checked FROM subreddits"
    )
    return {row[0]: row[1] for row in result} if success and result else {}

def update_last_post_checked(subreddit_name, last_post_checked):
    success, _ = execute_db_operation(
        "UPDATE subreddits SET last_post_checked = ? WHERE subreddit_name = ?",
        (last_post_checked, subreddit_name)
    )
    return success

def update_scan_time(subreddit_name):
    now_utc = datetime.now(timezone.utc)
    timestamp_numeric = now_utc.timestamp()
    success, _ = execute_db_operation(
        "UPDATE subreddits SET last_scan_time = ? WHERE subreddit_name = ?",
        (timestamp_numeric, subreddit_name)
    )
    return success

def is_cake_day(reddit, username):
    try:
        if has_been_wished(username):
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
                mark_as_wished(username)
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

def generate_cake_day_message(client, prompt, max_retries=3, initial_delay=1):
    """
    Generate a cake day message using the Gemini API with retry logic.

    Args:
        client: The Gemini API client
        prompt: The prompt to generate content from
        max_retries: Maximum number of retry attempts (default: 3)
        initial_delay: Initial delay between retries in seconds (default: 1)

    Returns:
        str: The generated message or a fallback message if all retries fail
    """
    attempt = 0
    while attempt < max_retries:
        try:
            response = client.models.generate_content(
                model="gemini-2.0-flash",
                contents=prompt,
            )
            return response.text

        except genai.RateLimitExceededError as e:
            if attempt < max_retries - 1:
                delay = initial_delay * (2 ** attempt) + random.uniform(0, 1)
                print(f"    ⚠️ Gemini API rate limit exceeded. Retrying in {delay:.2f} seconds... (Attempt {attempt + 1}/{max_retries})")
                time.sleep(delay)
            else:
                print(f"    ❌ Gemini API rate limit exceeded after {max_retries} attempts")
                return "Happy Cake Day! 🎂"  # Fallback message

        except genai.InvalidRequestError as e:
            print(f"    ❌ Invalid request to Gemini API: {str(e)}")
            return "Happy Cake Day! 🎂"  # Fallback for invalid requests

        except (genai.ServiceUnavailableError, genai.ConnectionError) as e:
            if attempt < max_retries - 1:
                delay = initial_delay * (2 ** attempt) + random.uniform(0, 1)
                print(f"    ⚠️ Gemini API connection error. Retrying in {delay:.2f} seconds... (Attempt {attempt + 1}/{max_retries})")
                time.sleep(delay)
            else:
                print(f"    ❌ Gemini API connection error after {max_retries} attempts: {str(e)}")
                return "Happy Cake Day! 🎂"  # Fallback message

        except Exception as e:
            print(f"    ❌ Unexpected error calling Gemini API: {str(e)}")
            return "Happy Cake Day! 🎂"  # Fallback message

        attempt += 1

    return "Happy Cake Day! 🎂"  # Final fallback if all retries fail

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
                    parent_text = parent.body[:250] if hasattr(parent, "body") else (parent.selftext[:250] if hasattr(parent, "selftext") else "(no text content)")
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
                current_text = item.body[:250]
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
                        sibling_text = sibling.body[:250] if hasattr(sibling, "body") else "(no text content)"
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
                post_text = item.selftext[:250] if item.selftext else "(no text content)"
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
                    comment_text = comment.body[:250] if hasattr(comment, "body") else "(no text content)"
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
            You are a Reddit bot that celebrates users' Cake Days. Your goal is to craft a thoughtful and relevant cake day wish for a user based on the surrounding conversation in a Reddit thread. Avoid overly quirky or exaggerated humor. Aim for a tone that is friendly, conversational, and appropriate for the subreddit.

            Here is the information about the context:

            Subreddit: r/{subreddit_name}
            Post Title: {post_title if post_title else item.title}
            Relevant Comment Chain:
            {comment_chain_context}

            Sentiment Analysis:
            - Average Sentiment Score: {average_sentiment_score:.2f} (Sentiment range is -1 to 1, -1 being very negative and 1 being very positive)
            - Most Extreme Sentiment: {most_extreme_sentiment["sentiment"]} (Text: "{most_extreme_sentiment["text"]}")
            - Sentiment Trend: {sentiment_trend}

            The user celebrating their Cake Day is "{item.author.name}". The user is {account_age_years} year{"s" if account_age_years > 1 else ""} old. Include their age somewhere in the cake day wish, if appropriate, but avoid phrases that directly connect their age on Reddit with their current activity or the post's topic. For example, do not say things like "[age] years on Reddit, and you're already [pondering/wondering/etc.]".

            Craft a cake day wish for "{item.author.name}" that acknowledges their cake day. Use the sentiment analysis to inform the tone of your message. If the overall sentiment is negative, offer a message of support or levity rather than forced cheerfulness. Consider the Reddit score of the post/comments; high scoring posts/comments are generally well-received.

            Your response should *only* be the cake day wish text, suitable for posting as a reply to the {'post' if item_type == 'post' else 'comment'}. Use Reddit formatting where appropriate (e.g., italics, bold). """

        # Tone Adjustment Based on Bot Karma (Using Karma Bands from the document):
        reddit_karma = ""
        if bot_karma < 1:
            reddit_karma = "low"
            gemini_message_prompt += f"""Your karma is low in r/{subreddit_name}. Use a strictly polite, neutral, and unobtrusive tone. Avoid any slang, humor, or embellishments. Ignore the context of the Relevant Comment Chain and keep the message very concise."""
        elif 1 <= bot_karma < 3:
            reddit_karma = "neutral"
            gemini_message_prompt += f"""Your karma is neutral r/{subreddit_name}. Use a polite and slightly warmer tone. A simple, positive emoji is acceptable. Keep the message concise. Use the context found in Relevant Comment Chain to inform your message."""
        elif 3 <= bot_karma < 5:
            reddit_karma = "slightly positive"
            gemini_message_prompt += f"""Your karma is slightly positive r/{subreddit_name}. Use a friendly and warm tone. Use a genuinely enthusiastic, warm, and celebratory tone. A few emojis are acceptable too. Use the context found in Relevant Comment Chain to inform your message. You may include a very short, positive, Reddit fun fact that is relevant to the context found in Relevant Comment Chain."""
        elif 5 <= bot_karma < 10:
            reddit_karma = "highly positive"
            gemini_message_prompt += f"""Your karma is highly positive r/{subreddit_name}. Use a celebratory tone, perhaps with a touch of light, widely understandable humor or a unique, positive flourish. Be creative, but avoid anything controversial. Use the context found in Relevant Comment Chain to inform your message."""
       
        # Call Gemini API to generate the message
        print(f"""    
            Subreddit: r/{subreddit_name}
            Post Title: {post_title if post_title else item.title}
            Bot Karma: {reddit_karma}
            Relevant Comment Chain:\n
              {comment_chain_context}\n
            Sentiment Analysis:
              - Average Sentiment Score: {average_sentiment_score:.2f} 
              - Most Extreme Sentiment: {most_extreme_sentiment["sentiment"]} (Text: "{most_extreme_sentiment["text"]}")
              - Sentiment Trend: {sentiment_trend}
        """)

        try:
            client = get_gemini_client()
            gemini_message = generate_cake_day_message(client, gemini_message_prompt)
        except Exception as e:
            print(f"    ⚠️ Failed to generate message using Gemini API, using fallback: {str(e)}")
            gemini_message = "Happy Cake Day! 🎂"  # Ultimate fallback message

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
    clear_expired_wished_users()  # Clear expired wished users at the start
    start_time = time.time()  # Store the start timestamp
    reddit_time = time.time()  # Store the start timestamp to time each reddit scan
    reddit = get_reddit_instance()

    # Display the bot's total comment karma once at the start
    bot_user = reddit.redditor(REDDIT_USERNAME)
    print(f"\n✨ Bot's total comment karma: {bot_user.comment_karma}\n")

    subreddit_info = get_subreddit_info_from_database()

    if not subreddit_info:
        print("No subreddits found in the database.")
    else:
        print("Scanning subreddits for Cake Days...")
        for subreddit_name, last_post_checked in subreddit_info.items():
            # Calculate the bot's overall score in the subreddit once per subreddit
            print(f"\n🔍 Scanning r/{subreddit_name}...")
            bot_score = get_bot_comment_score(reddit, subreddit_name)

            new_last_post_checked = process_subreddit(reddit, subreddit_name, last_post_checked, bot_score)
            update_last_post_checked(subreddit_name, new_last_post_checked)
            update_scan_time(subreddit_name)

            # Calculate and print elapsed time
            total_elapsed_time = time.time() - start_time
            hours, remainder = divmod(total_elapsed_time, 3600)
            minutes, seconds = divmod(remainder, 60)

            reddit_elapsed_time = time.time() - reddit_time
            rhours, rremainder = divmod(reddit_elapsed_time, 3600)
            rminutes, rseconds = divmod(rremainder, 60)

            print(f"✅ Finished scanning r/{subreddit_name}. Time taken to scan: {int(rhours)}h {int(rminutes)}m {rseconds:.2f}s")
            print(f"🕒 Total time taken: {int(hours)}h {int(minutes)}m {seconds:.2f}s")

            reddit_time = time.time()  # Reset the Reddit scan timer for the next subreddit

    print("\nScan complete.")
