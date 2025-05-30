import sqlite3
import praw
import prawcore
import random
from datetime import datetime, timezone, timedelta
import hashlib
import requests
import time
from google import genai  # Import the genai library
from pytz import timezone as pytz_timezone  # Rename pytz's timezone to avoid conflicts
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer  # Import sentiment analyzer
from config import CLIENT_ID, CLIENT_SECRET, USER_AGENT, REDDIT_USERNAME, REDDIT_PASSWORD, DATABASE_NAME, API_CALL_DELAY, GEMINI_API_KEY, GEMINI_MODELS  # Add GEMINI_MODELS
from models import Database, SubredditManager, WishedUsersManager
from PIL import Image
from io import BytesIO
from pathlib import Path

# Create global instance of SentimentIntensityAnalyzer for efficiency
SENTIMENT_ANALYZER = SentimentIntensityAnalyzer()

# Cache for sentiment scores to avoid recalculation
SENTIMENT_CACHE = {}

# Create an images directory if it doesn't exist
IMAGES_DIR = Path("images")
IMAGES_DIR.mkdir(exist_ok=True)

# Initialize global instances
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
    Analyze the sentiment of a given text using the global Vader SentimentIntensityAnalyzer instance.
    Uses caching to avoid recalculating sentiment for the same text.

    Args:
        text (str): The text to analyze.

    Returns:
        str: The overall sentiment ('positive', 'neutral', or 'negative').
    """
    # Use cache if available
    if text in SENTIMENT_CACHE:
        sentiment_scores = SENTIMENT_CACHE[text]
    else:
        # Calculate and cache the sentiment
        sentiment_scores = SENTIMENT_ANALYZER.polarity_scores(text)
        SENTIMENT_CACHE[text] = sentiment_scores

    if sentiment_scores['compound'] >= 0.05:
        return "positive"
    elif sentiment_scores['compound'] <= -0.05:
        return "negative"
    else:
        return "neutral"

# Cleanup cache periodically (every 1000 items)
def cleanup_sentiment_cache(max_size=1000):
    """Clean up the sentiment cache if it grows too large."""
    if len(SENTIMENT_CACHE) > max_size:
        SENTIMENT_CACHE.clear()

def generate_cake_day_message(client, model_name, prompt, image_path=None):
    """Generate a cake day message using the Gemini API."""
    try:
        if not client or not model_name:
            return "Happy Cake Day! 🎂"
            
        print(f"    🤖 Generating message with model: {model_name}")
        
        if image_path:  
            try:
                img = Image.open(image_path)  # Image is already in RGB from download_and_process_image
                print("    🖼️ Including image in prompt")
                contents = [img, prompt]
            except Exception as e:
                print(f"    ⚠️ Error loading image: {str(e)}")
                contents = prompt
        else:
            contents = prompt
            
        response = client.models.generate_content(
            model=model_name,
            contents=contents
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
                    parent_text = parent.body[:500] if hasattr(parent, "body") else (parent.selftext[:500] if hasattr(parent, "selftext") else "(no text content)")
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
                })                # Fetch limited top-level comments efficiently
                submission = item
                submission.comments.replace_more(limit=0)  # Don't expand any MoreComments
                for comment in list(submission.comments)[:10]:  # Limit to 10 comments
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
            print(f"    ⚠️ Error collecting comment chain context: {e}")        # Calculate sentiment statistics using the global analyzer instance
        sentiment_scores = [analyze_sentiment(entry["text"]) for entry in comment_chain_context]
        average_sentiment_score = sum([SENTIMENT_ANALYZER.polarity_scores(entry["text"])["compound"] for entry in comment_chain_context]) / len(comment_chain_context)
        most_extreme_sentiment = max(comment_chain_context, key=lambda x: abs(SENTIMENT_ANALYZER.polarity_scores(x["text"])["compound"]))
        sentiment_trend = "positive" if average_sentiment_score > 0 else "negative" if average_sentiment_score < 0 else "neutral"        # Construct the Gemini prompt with bot performance data
        bot_total_score = bot_performance[0] if bot_performance else 0
        bot_comment_count = bot_performance[1] if bot_performance else 0
        bot_karma = (bot_total_score / bot_comment_count) if bot_comment_count > 0 else 0
          # Get image context from both the comment and parent post if available
        image_info = None
        if item_type == 'comment':
            # First check if the comment itself has any images
            comment_image_info = get_post_images(item)
            
            # Then get the top-level post
            top_post = item
            while hasattr(top_post, 'parent') and not isinstance(top_post, praw.models.Submission):
                top_post = top_post.parent()
            post_image_info = get_post_images(top_post)
            
            # Combine image information, prioritizing the comment's images if they exist
            if comment_image_info['type']:
                image_info = comment_image_info
            else:
                image_info = post_image_info
        else:
            image_info = get_post_images(item)
            
        image_path = image_info['paths'][0] if image_info and image_info['paths'] else None
        image_context = ""
        if image_info and image_info['type']:
            if image_info['is_main_content']:
                if image_info['type'] == 'direct_image':
                    image_context = "This is an image post."
                elif image_info['type'] == 'gallery':
                    image_context = f"This is a gallery post with {image_info['total_count']} images (showing first)."
            else:
                image_context = f"This post contains {image_info['total_count']} embedded image{'s' if image_info['total_count'] > 1 else ''}."

        gemini_message_prompt = f"""
You are a witty and cheeky AI-powered Reddit bot with a playful personality. Your mission is to celebrate Cake Days with clever, sometimes irreverent humor, while still being genuinely supportive. Think of yourself as that smart friend who can't help but add a dash of wit to everything, but knows when to dial it back. Adapt your sass level based on the subreddit and conversation tone.

Personality Traits:
- Witty and quick with wordplay
- Playfully sarcastic (when appropriate)
- Cleverly observant
- Good at reading the room
- Knows when to be serious vs playful

Context:
Subreddit: r/{subreddit_name}
Post Title: {post_title if post_title else item.title}
Post Type: {item.post_hint if hasattr(item, "post_hint") else "text"}
{image_context if image_context else ""}
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

**Critical Instructions for Message Crafting:**
- Primary Focus: Deliver a warm Cake Day wish and follow it with a *highly specific, insightful, and engaging* comment that directly relates to the immediate context of the user's post or parent comment. Your goal is to add clear value and feel like a thoughtful participant.

- **The Peril of Platitudes & Generic Comments:**
    - Your biggest risk is sounding generic, like you're stating the obvious or using common, unoriginal phrases (e.g., "some things stand the test of time," "it's fascinating how X differs," "complex films are good"). THESE MUST BE AVOIDED.
    - Generic comments are frequently downvoted and make the bot seem unhelpful or like "AI slop."

- Message Structure:
    1. **Lead with "Happy Cake Day! 🎂"** (Adjust the warmth and enthusiasm of this greeting based on the conversation's sentiment and your current karma-defined personality level).
    2. **Follow with *Specific & Insightful* Contextual Engagement:** This is the core of your value. Your comment here MUST:
        - **Directly reference a specific detail, nuance, phrase, or idea** from the user's post or the parent comment. Quote a short key phrase if it helps show you're addressing something specific.
        - **Offer a genuine, non-obvious insight, elaboration, or connection** related to that specific detail. Don't just summarize or rephrase what they said. Build on it, offer a gentle reframe, highlight an interesting implication, or share a highly relevant, concise piece of information.
        - **Affirm positively if appropriate:** Phrases like "That's a great point you made about [specific detail]..." or "I love your take on [specific idea]..." can be effective starting points for your elaboration.
        - **Ensure your observation is unique to *this* context** and not something that could be said in many other similar threads.

- **Options for the *Nature* of Your Contextual Engagement (apply the specificity/insight rules above to whichever you choose):**
    - **Insightful Observation/Elaboration:** Analyze or expand on their specific point. (e.g., "Your take on [specific detail X] is spot on; it also reminds me how [related concept Y] comes into play...").
    - **Targeted Factual Nugget (if truly novel and relevant):** A "Did You Know?" or "On This Day" fact is only acceptable if it's *surprisingly relevant* to a *specific detail* the user mentioned and isn't common knowledge. High bar here.
    - **Specifically Resonant Emotion/Shared Experience:** If sharing an emotion, it must be tied directly to a very specific scenario or detail the user described, e.g., "I felt that exact same frustration when [specific game mechanic mentioned by user] glitched out!"

- **Example of What to AVOID (Generic/Platitude-Based Contextual Remarks AFTER 'Happy Cake Day!'):**
    - "...That's an interesting perspective." (Vague)
    - "...Seeing [Topic] as your favorite just proves some things truly stand the test of time." (Cliché)
    - "...It's impressive how some films truly demand patient deciphering." (Generic praise)
    - "...It's fascinating how specific interactions can differ." (States the obvious for the context)

- **Examples of What to AIM FOR (Specific & Insightful Contextual Remarks AFTER 'Happy Cake Day!'):**
    - User `adorcial` says MLP characters' "bickering" is "banter" making them "resilient": "Happy Cake Day, adorcial 🎂 I love your point about their "bickering" being more like banter that makes their relationship more resilient – it really captures the dynamic of two strong-willed ponies finding strength in their similarities." (Specific, affirming, elaborates with insight)
    - User posts about a specific technical challenge in Fortnite: "Happy Cake Day! 🎂 May your in-game performance be as buttery smooth as your cake. Hopefully, figuring out the exact -disabletexturestreaming command doesn't require as many attempts as finding the perfect landing spot!" (Specific to game, command, and common game experience, uses playful analogy)
    - User asks about understanding a complex movie like Inception: "Happy Cake Day! 🎂 Inception is definitely one of those films where, as you said, each rewatch feels like peeling back another layer of the dream – those subtle clues about Cobb's state of mind are fascinating to uncover!" (Specific to film, user's sentiment, and a particular aspect of watching it).

- **Personality Integration:**
    - Your "witty, cheeky, playful" personality should be expressed *through* your specific and insightful engagement. A clever turn of phrase about a specific detail, a playful analogy relevant to the context (like the Fortnite "landing spot"), or a warm, enthusiastic affirmation of a user's specific point are great ways to do this.
    - Avoid generic jokes or personality quirks that are disconnected from the contextual remark.

- **Brevity is Key:** Even with specificity and insight, the entire message (Cake Day wish + contextual comment) should remain concise, typically 2-3 sentences.


Message Guidelines Based on Performance:"""

        # Note: Remove the existing karma bands and replace with this more specific guidance
        if bot_karma < 0 or item.score < 1:
            gemini_message_prompt += f"""    
    IMPORTANT: Previous responses were not well-received in r/{subreddit_name}.
    - Study the conversation carefully. Your main goal is to avoid further negative reception.
    - Match the subreddit's general tone and style meticulously.
    - Keep the message brief, relevant, and impeccably polite.
    - Focus on being genuinely respectful. Convey a hint of warmth if possible, but avoid any attempts at humor or distinct personality beyond this.
    - If conversation is technical/serious, be strictly professional.
    - If conversation is casual, be natural, understated, and warm.
    - Only use emojis if they are prevalent and clearly appropriate.
    - Priority: Blend In & Be Genuinely Polite > Celebrate"""
        elif bot_karma == 0 or bot_comment_count == 0:
            gemini_message_prompt += f"""
    IMPORTANT: First interaction in r/{subreddit_name}. Aim to make a positive, approachable first impression.
    - Study the provided conversation context carefully.
    - Keep your message concise (1-2 short sentences).
    - Make a relevant and thoughtful contribution.
    - Match the tone and vocabulary of other comments, leaning towards warmth and friendliness.
    - You can introduce a *gentle touch* of your bot's positive personality traits (e.g., a kind observation, a slight warmth in your phrasing, or a subtle, positive expression of interest in the topic).
    - Avoid anything that could be seen as controversial, edgy, or too attention-seeking. Think of it as a friendly, polite introduction that hints at a pleasant personality.
    - For technical/professional discussions, maintain formality but ensure your tone is approachable.
    - Only use emoji if present in other comments and it fits a warm, introductory tone.
    - Priority: Relevance & Positive First Impression > Celebrate"""
        elif 0 < bot_karma < 3:
            gemini_message_prompt += f"""
    IMPORTANT: Slowly building acceptance in r/{subreddit_name}. Focus on adding value while showcasing a positive, engaging personality.
    - Study the conversation context to identify the main topic/theme and the overall sentiment.
    - Keep your message concise (2 sentences maximum).
    - Make a meaningful contribution that ideally sparks a little positive interest or connection.
    - Match the community's vocabulary. Let your defined personality shine through in a *warm, approachable, and positive* way.
    - You can use gentle, contextually appropriate humor if it clearly fits the subreddit's tone and the specific discussion, or a cleverly kind observation. The aim is to build rapport and show you're more than just a fact-delivery system.
    - If the conversation is lighthearted, match that energy with your positive personality.
    - If the conversation is serious, maintain professionalism but still aim for an approachable and warm tone.
    - Avoid strong sarcasm or overt cheekiness; these are better reserved for when your positive presence is more established (higher karma).
    - For specialty subreddits, demonstrate topic understanding in an engaging way.
    - Only use emoji if others are using them and it enhances your warm, positive tone.
    - Priority: Add Value & Positive Connection > Celebrate > Overtly Stand Out"""
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
      * A fun birthday quote from someone famous that is relevant to the conversation
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
                # Only pass image if it's meaningful to the context
                use_image = image_path and image_info['is_main_content']
                gemini_message = generate_cake_day_message(
                    client, 
                    model_name, 
                    gemini_message_prompt,
                    image_path if use_image else None
                )
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
            time.sleep(API_CALL_DELAY)  # Be mindful of rate limits        # Load comments efficiently - only expand first level
        post.comments.replace_more(limit=0)  # Don't expand any MoreComments
        for comment in list(post.comments)[:50]:  # Limit to first 50 top-level comments
            if comment.author:
                if process_item(reddit, comment, "comment", subreddit_name, post.title, bot_performance=bot_score):
                    cake_day_count += 1
                time.sleep(API_CALL_DELAY)

    print(f"\n🎉 Total Cake Days found in r/{subreddit_name}: {cake_day_count} {"" if cake_day_count == 0 else "🎉🎉"}")
    return new_last_post_checked

def get_bot_comment_score(reddit, subreddit_name, days_to_check=30, cache_ttl=900):
    """
    Calculate the overall score of the bot's comments in a subreddit with caching.

    Args:
        reddit: The PRAW Reddit instance.
        subreddit_name: The name of the subreddit.
        days_to_check: Number of days to look back for comments (default: 30)
        cache_ttl: Time in seconds before cache expires (default: 15 minutes)

    Returns:
        tuple: (total_score, comment_count)
    """
    # Check cache first
    cached_score = db.get_bot_performance(subreddit_name, cache_ttl)
    if cached_score:
        total_score, comment_count = cached_score
        print(f"\n📈 Summary for r/{subreddit_name} (cached):")
        print(f"  - Total comments found: {comment_count}")
        print(f"  - Total score: {total_score:+}")
        print(f"  - Average score per comment: {(total_score/comment_count if comment_count else 0):+.2f}\n")
        return total_score, comment_count

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

        # Cache the results
        db.update_bot_performance(subreddit_name, total_score, comment_count)

        print(f"\n📈 Summary for r/{subreddit_name} (fresh):")
        print(f"  - Total comments found: {comment_count}")
        print(f"  - Total score: {total_score:+}")
        print(f"  - Average score per comment: {(total_score/comment_count if comment_count else 0):+.2f}\n")
        
        return total_score, comment_count
    except Exception as e:
        print(f"⚠️ Error fetching bot comments for r/{subreddit_name}: {e}")
        return 0, 0

def download_and_process_image(url, max_retries=3, cache_ttl=3600):  # 1 hour TTL by default
    """
    Downloads and processes an image from a URL, returning the local path.
    
    Args:
        url: The URL of the image to download
        max_retries: Maximum number of retry attempts
        cache_ttl: Time in seconds to keep cached images (default: 1 hour)
        
    Returns:
        str: Path to the downloaded image, or None if download failed
    """
    try:
        # Generate a unique filename based on the URL
        filename = hashlib.md5(url.encode()).hexdigest() + '.jpg'
        filepath = IMAGES_DIR / filename
        
        # If we already have this image, check if it's still valid
        if filepath.exists():
            file_age = time.time() - filepath.stat().st_mtime
            if file_age < cache_ttl:
                return str(filepath)
            else:
                filepath.unlink()  # Delete expired cache
            
        # Download the image with retries
        for attempt in range(max_retries):
            try:
                response = requests.get(url, timeout=10)
                response.raise_for_status()
                
                # Open and process image
                img = Image.open(BytesIO(response.content))
                
                # Convert to RGB if needed (handles PNG, RGBA, etc.)
                if img.mode in ('RGBA', 'P'):
                    img = img.convert('RGB')
                
                # Resize if too large (max 1024px on longest side)
                if max(img.size) > 1024:
                    ratio = 1024 / max(img.size)
                    new_size = tuple(int(dim * ratio) for dim in img.size)
                    img = img.resize(new_size, Image.Resampling.LANCZOS)
                
                # Save the processed image with quality optimization
                img.save(filepath, 'JPEG', quality=85, optimize=True)
                return str(filepath)
                
            except requests.exceptions.RequestException as e:
                if attempt == max_retries - 1:
                    print(f"    ⚠️ Failed to download image after {max_retries} attempts: {str(e)}")
                    return None
                time.sleep(2 ** attempt)  # Exponential backoff
            except Exception as e:
                print(f"    ⚠️ Error processing image: {str(e)}")
                return None
                
    except Exception as e:
        print(f"    ⚠️ Error processing image: {str(e)}")
        return None

def get_post_images(item):
    """
    Extracts and downloads images from a Reddit post or comment, with context.
    
    Args:
        item: A Reddit post or comment object
        
    Returns:
        dict: Information about found images with their context
            {
                'paths': list of image file paths,
                'type': 'direct_image'|'preview'|'gallery'|None,
                'is_main_content': bool indicating if image is the main content,
                'total_count': total number of images available
            }
    """
    result = {
        'paths': [],
        'type': None,
        'is_main_content': False,
        'total_count': 0
    }
    
    try:
        # Handle different types of posts
        if hasattr(item, 'url'):
            # Direct image links
            if any(item.url.lower().endswith(ext) for ext in ('.jpg', '.jpeg', '.png', '.gif')):
                if path := download_and_process_image(item.url):
                    result.update({
                        'paths': [path],
                        'type': 'direct_image',
                        'is_main_content': True,
                        'total_count': 1
                    })
            # Image posts with previews
            elif hasattr(item, 'preview') and 'images' in item.preview and item.preview['images']:
                if path := download_and_process_image(item.preview['images'][0]['source']['url']):
                    result.update({
                        'paths': [path],
                        'type': 'preview',
                        'is_main_content': item.is_self is False,  # True if it's a link post
                        'total_count': len(item.preview['images'])
                    })
            # Gallery posts
            elif hasattr(item, 'is_gallery') and item.is_gallery and hasattr(item, 'media_metadata'):
                media_id = next((id for id in item.media_metadata if item.media_metadata[id]['e'] == 'Image'), None)
                if media_id and (path := download_and_process_image(item.media_metadata[media_id]['s']['u'])):
                    result.update({
                        'paths': [path],
                        'type': 'gallery',
                        'is_main_content': True,
                        'total_count': len(item.media_metadata)
                    })
                                
    except Exception as e:
        print(f"    ⚠️ Error extracting images: {str(e)}")
        
    return result

def cleanup_old_images(max_age=900):  # 15 minutes default
    """
    Clean up old cached images to manage disk space.
    
    Args:
        max_age: Maximum age of images in seconds before deletion
    """
    try:
        current_time = time.time()
        for image_file in IMAGES_DIR.glob('*.jpg'):
            file_age = current_time - image_file.stat().st_mtime
            if file_age > max_age:
                try:
                    image_file.unlink()
                    print(f"    🗑️ Cleaned up old image: {image_file.name}")
                except Exception as e:
                    print(f"    ⚠️ Failed to delete old image {image_file.name}: {e}")
    except Exception as e:
        print(f"    ⚠️ Error during image cleanup: {e}")

if __name__ == "__main__":
    # Initialize start time for total execution
    total_start_time = time.time()
    
    # Initialize Reddit instance
    reddit = get_reddit_instance()
    wished_users_mgr.clear_expired()
    cleanup_old_images()  # Clean up old images before starting new scan
    subreddit_info = subreddit_mgr.get_info()
    
    for subreddit_name, (last_post_checked, last_scan_time) in subreddit_info.items():
        print(f"\n🔍 Processing r/{subreddit_name}")
        # Start timing this subreddit
        subreddit_start_time = time.time()
        
        bot_score = get_bot_comment_score(reddit, subreddit_name)
        new_last_post_checked = process_subreddit(reddit, subreddit_name, last_post_checked, bot_score)
        subreddit_mgr.update_last_post_checked(subreddit_name, new_last_post_checked)
        subreddit_mgr.update_scan_time(subreddit_name)
          # Calculate and print elapsed time for this subreddit
        subreddit_elapsed_time = time.time() - subreddit_start_time
        hours = int(subreddit_elapsed_time // 3600)
        minutes = int((subreddit_elapsed_time % 3600) // 60)
        seconds = subreddit_elapsed_time % 60
        print(f"⏱️  Time to process r/{subreddit_name}: {f'{hours}h ' if hours > 0 else ''}{f'{minutes}m ' if minutes > 0 or hours > 0 else ''}{seconds:.2f}s")

    # Calculate and print total execution time
    total_elapsed_time = time.time() - total_start_time
    hours = int(total_elapsed_time // 3600)
    minutes = int((total_elapsed_time % 3600) // 60)
    seconds = total_elapsed_time % 60
    print(f"\n🏁 Total execution time: {f'{hours}h ' if hours > 0 else ''}{f'{minutes}m ' if minutes > 0 or hours > 0 else ''}{seconds:.2f}s")
