import re
import csv
import json
import time
import random
import httpx
from urllib.parse import quote
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# Instagram query hash used for the GraphQL request
INSTAGRAM_QUERY_HASH = "97b41c52301f77ce508f55e66d17620e"

# List of common User-Agent strings to randomly choose from
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Firefox/115.0",
]

def get_random_user_agent() -> str:
    """Randomly select a User-Agent string."""
    return random.choice(USER_AGENTS)

def get_hashtag_posts(hashtag: str, scroll_times=1) -> list:
    """
    Opens the Instagram hashtag page for the given hashtag,
    scrolls down a few times to load posts, and extracts the base post URLs.
    Only returns URLs in the format "https://www.instagram.com/p/{shortcode}/".
    """
    encoded_hashtag = quote(hashtag)
    url = f"https://www.instagram.com/explore/tags/{encoded_hashtag}/"
    
    options = Options()
    # Uncomment the following line to run in headless mode if needed:
    # options.add_argument("--headless")
    options.add_argument("user-data-dir=/Users/xuhuirong/Library/Application Support/Google/Chrome")
    options.add_argument("--profile-directory=Person 2")
    
    driver = webdriver.Chrome(options=options)
    driver.get(url)
    
    # If a login page is detected, prompt the user to log in manually.
    while "accounts/login" in driver.current_url:
        print("Login page detected, please log in manually in the opened browser and press Enter to continue...")
        input()
        time.sleep(3)
    
    try:
        # Wait up to 60 seconds for at least one post link to appear
        WebDriverWait(driver, 60).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "a[href*='/p/']"))
        )
    except Exception as e:
        print("Hashtag page timed out or has an unexpected structure:", e)
        driver.quit()
        return []
    
    # Scroll down to load more posts
    for _ in range(scroll_times):
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(3)
    
    # Extract post URLs from the page
    post_elements = driver.find_elements(By.CSS_SELECTOR, "a[href*='/p/']")
    post_urls = set()
    for elem in post_elements:
        href = elem.get_attribute("href")
        if href and "/p/" in href and "liked_by" not in href and "comments" not in href:
            shortcode = href.split("/p/")[-1].split("/")[0]
            base_url = f"https://www.instagram.com/p/{shortcode}/"
            post_urls.add(base_url)
    
    driver.quit()
    return list(post_urls)

def get_cookies_from_driver(driver) -> dict:
    """
    Retrieves cookies from the Selenium driver and returns them as a dictionary.
    """
    selenium_cookies = driver.get_cookies()
    cookies = {}
    for cookie in selenium_cookies:
        cookies[cookie['name']] = cookie['value']
    return cookies

def scrape_post(url_or_shortcode: str, cookies: dict) -> dict:
    """
    Uses httpx to send a POST request to Instagram's GraphQL endpoint to
    retrieve detailed post data (including comment data) for a given post.
    Returns the JSON part in data["shortcode_media"].
    """
    if "http" in url_or_shortcode:
        shortcode = url_or_shortcode.split("/p/")[-1].split("/")[0]
    else:
        shortcode = url_or_shortcode
    print(f"Scraping post: {shortcode}")
    
    variables = json.dumps({
        "shortcode": shortcode,
        "first": 50,
        "after": None
    }, separators=(',', ':'))
    body = f"query_hash={INSTAGRAM_QUERY_HASH}&variables={quote(variables)}"
    graphql_url = "https://www.instagram.com/graphql/query"
    
    headers = {
        "content-type": "application/x-www-form-urlencoded",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
    }
    
    response = httpx.post(url=graphql_url, headers=headers, data=body, cookies=cookies, timeout=60.0)
    print("Response text:", response.text)
    try:
        data = response.json()
        # Based on the current response structure, comment data is typically in data["shortcode_media"]["edge_media_to_comment"]
        return data["data"]["shortcode_media"]
    except Exception as e:
        print(f"Error parsing JSON for post {shortcode}: {e}")
        return {}

def get_user_profile(username: str, client: httpx.Client, cookies: dict = None) -> dict:
    """
    Calls the Instagram API to get user information.
    The returned data's "data" -> "user" contains the user's biography and other info.
    If the request fails, it will retry 3 times.
    """
    url = f"https://i.instagram.com/api/v1/users/web_profile_info/?username={username}"
    headers = {
        "User-Agent": get_random_user_agent(),
        "x-ig-app-id": "936619743392459",
        "Accept": "application/json",
        "Referer": "https://www.instagram.com/"
    }
    retries = 3
    for attempt in range(retries):
        try:
            response = client.get(url, headers=headers, cookies=cookies, timeout=30.0)
            if response.status_code == 200:
                data = response.json()
                # Uncomment the following line during debugging to view the complete JSON data returned
                # print(f"{username} returned data:", json.dumps(data, indent=2, ensure_ascii=False))
                return data.get("data", {}).get("user", {})
            else:
                print(f"[{username}] Request failed, status code: {response.status_code}")
        except Exception as e:
            print(f"[{username}] Error fetching information (attempt {attempt + 1}): {e}")
        sleep_time = 2 + random.random() * 2
        print(f"Waiting {sleep_time:.1f} seconds before retrying...")
        time.sleep(sleep_time)
    return {}

def extract_comment_usernames(post_json: dict) -> list:
    """
    Extracts a list of comment usernames from the post JSON data.
    If the data contains 'edge_media_to_parent_comment', it is used;
    otherwise, 'edge_media_to_comment' is used.
    """
    usernames = []
    comment_data = None
    if "edge_media_to_parent_comment" in post_json:
        comment_data = post_json["edge_media_to_parent_comment"]
    elif "edge_media_to_comment" in post_json:
        comment_data = post_json["edge_media_to_comment"]
    
    if comment_data:
        for edge in comment_data.get("edges", []):
            node = edge.get("node", {})
            owner = node.get("owner", {})
            username = owner.get("username")
            if username and username not in usernames:
                usernames.append(username)
    return usernames

def extract_phone_from_bio(bio: str) -> str:
    """
    Uses a regular expression to match a phone number from the biography.
    The matching pattern can be adjusted as needed.
    """
    phone_pattern = re.compile(r'(\+?\d[\d\s\-]{8,}\d)')
    matches = phone_pattern.findall(bio)
    if matches:
        return matches[0]
    return ""

def extract_email_from_bio(bio: str) -> str:
    """
    Uses a regular expression to match an email address from the biography.
    """
    email_pattern = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')
    matches = email_pattern.findall(bio)
    if matches:
        return matches[0]
    return ""

def extract_link_from_bio(bio: str) -> str:
    """
    Uses a regular expression to match a URL from the biography.
    """
    link_pattern = re.compile(r'https?://[^\s]+')
    matches = link_pattern.findall(bio)
    if matches:
        return matches[0]
    return ""

def write_profiles_to_csv(profiles: list, filename: str):
    """
    Saves the obtained user information to a CSV file,
    including the fields: username, biography, phone_number, email, and link.
    """
    with open(filename, "w", newline='', encoding="utf-8") as f:
        fieldnames = ["username", "biography", "phone_number", "email", "link"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for profile in profiles:
            writer.writerow({
                "username": profile.get("username", ""),
                "biography": profile.get("biography", ""),
                "phone_number": profile.get("phone_number", ""),
                "email": profile.get("email", ""),
                "link": profile.get("link", "")
            })

def read_usernames_from_csv(filename: str) -> set:
    """
    Reads the usernames mentioned in comments from a CSV file.
    Assumes the CSV file contains a column named "comment_username".
    Returns a set of unique usernames.
    """
    usernames = set()
    try:
        with open(filename, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if "comment_username" in row and row["comment_username"]:
                    usernames.add(row["comment_username"].strip())
    except Exception as e:
        print(f"Failed to read file {filename}: {e}")
    return usernames

def main():
    hashtag = "保健品"
    print(f"Fetching posts for #{hashtag}...")
    post_urls = get_hashtag_posts(hashtag, scroll_times=1)
    print(f"Found {len(post_urls)} posts for #{hashtag}.")
    
    options = Options()
    # options.add_argument("--headless")
    options.add_argument("user-data-dir=/Users/xuhuirong/Library/Application Support/Google/Chrome")
    options.add_argument("--profile-directory=Person 2")
    driver = webdriver.Chrome(options=options)
    driver.get("https://www.instagram.com")
    time.sleep(5)
    cookies = get_cookies_from_driver(driver)
    driver.quit()
    
    results = []  # (post_url, comment_username)
    comment_usernames = set()
    
    for post_url in post_urls:
        print(f"Processing post: {post_url}")
        post_json = scrape_post(post_url, cookies)
        if not post_json:
            print(f"Failed to get post data: {post_url}")
            continue
        usernames = extract_comment_usernames(post_json)
        if usernames:
            print(f"Found comment usernames: {usernames}")
            for username in usernames:
                comment_usernames.add(username)
                results.append((post_url, username))
        else:
            print(f"No comments found for {post_url}.")
        time.sleep(2)
    
    # Write the comments data into a CSV file
    csv_comments_filename = "comments.csv"
    with open(csv_comments_filename, "w", newline='', encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["post_url", "comment_username"])
        writer.writerows(results)
    
    print(f"Saved comments to {csv_comments_filename}")
    
    # Now get user profile information for the unique usernames extracted from comments
    profiles = []
    with httpx.Client() as client:
        for username in comment_usernames:
            print(f"Fetching profile information for user {username}...")
            profile = get_user_profile(username, client, cookies)
            if profile:
                bio = profile.get("biography", "")
                phone = extract_phone_from_bio(bio)
                email = extract_email_from_bio(bio)
                link = extract_link_from_bio(bio)
                profile["phone_number"] = phone
                profile["email"] = email
                profile["link"] = link
                profiles.append(profile)
            wait_time = random.uniform(30, 60)
            print(f"Request complete, waiting {wait_time:.1f} seconds...")
            time.sleep(wait_time)
    
    output_csv = "profiles_phone.csv"
    write_profiles_to_csv(profiles, output_csv)
    print(f"User profile information saved to {output_csv}")

if __name__ == "__main__":
    main()
