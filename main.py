"""
Sorare Score Monitor
--------------------
Polls the Sorare GraphQL API every 15 minutes for the current fixture's
player scores. When any player reaches 100+ points in a game, posts a
tweet via the Twitter/X API.

Run:
    python3 main.py
"""

import os
import time
import logging
import requests
import tweepy

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SORARE_GRAPHQL_URL = "https://api.sorare.com/graphql"

TWITTER_API_KEY = os.environ["TWITTER_API_KEY"]
TWITTER_API_SECRET = os.environ["TWITTER_API_SECRET"]
TWITTER_ACCESS_TOKEN = os.environ["TWITTER_ACCESS_TOKEN"]
TWITTER_ACCESS_TOKEN_SECRET = os.environ["TWITTER_ACCESS_TOKEN_SECRET"]

POLL_INTERVAL_SECONDS = 15 * 60
SCORE_THRESHOLD = 50

# ---------------------------------------------------------------------------
# GraphQL helpers
# ---------------------------------------------------------------------------

FIXTURES_SLUGS_QUERY = """
query GetRecentFixtureSlugs {
  so5 {
    so5Fixtures(first: 3) {
      nodes {
        id
        slug
        gameWeek
      }
    }
  }
}
"""

FIXTURE_GAMES_QUERY = """
query GetFixtureGames($slug: String!) {
  so5 {
    so5Fixture(slug: $slug) {
      id
      slug
      gameWeek
      games {
        id
        homeTeam { name }
        awayTeam { name }
      }
    }
  }
}
"""

PLAYERS_QUERY = """
query GetGamePlayers($gameId: ID!) {
  football {
    game(id: $gameId) {
      players {
        slug
        displayName
        position
      }
    }
  }
}
"""

def gql(query: str, variables: dict = None) -> dict:
    """Execute a GraphQL query and return parsed JSON data."""
    payload = {"query": query}
    if variables:
        payload["variables"] = variables

    try:
        resp = requests.post(
            SORARE_GRAPHQL_URL,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=30,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.error("HTTP error calling Sorare API: %s", exc)
        return {}

    data = resp.json()
    if "errors" in data:
        for err in data["errors"]:
            logger.error("Sorare GraphQL error: %s", err.get("message"))
        return {}

    return data.get("data", {})


def fetch_recent_fixtures() -> list:
    """
    Return the most recent so5Fixtures with their games.
    Sorare does not allow selecting 'games' inside a fixture list,
    so we first fetch fixture slugs, then each fixture individually.
    """
    data = gql(FIXTURES_SLUGS_QUERY)
    try:
        stubs = data["so5"]["so5Fixtures"]["nodes"]
    except (KeyError, TypeError):
        logger.error("Could not parse fixture slugs from Sorare response.")
        return []

    fixtures = []
    for stub in stubs[:2]:  # Only check the two most recent
        slug = stub["slug"]
        fixture_data = gql(FIXTURE_GAMES_QUERY, {"slug": slug})
        try:
            fixture = fixture_data["so5"]["so5Fixture"]
            fixtures.append(fixture)
        except (KeyError, TypeError):
            logger.warning("Could not fetch games for fixture: %s", slug)

    return fixtures


def fetch_players_for_game(game_id: str) -> list:
    """Return list of {slug, displayName, position} for a game."""
    # The football.game field expects a bare UUID, not the prefixed "Game:<uuid>" form
    raw_id = game_id.split(":")[-1] if ":" in game_id else game_id
    data = gql(PLAYERS_QUERY, {"gameId": raw_id})
    try:
        return data["football"]["game"]["players"]
    except (KeyError, TypeError):
        return []


def build_score_query(fixture_slug: str, game_id: str, players: list) -> str:
    """
    Build a GraphQL query that fetches so5Score for every player in a game
    using field aliases (one request per game).
    """
    if not players:
        return ""

    aliases = []
    for i, player in enumerate(players):
        slug = player["slug"]
        safe_alias = f"p{i}_{slug.replace('-', '_')}"
        aliases.append(
            f'{safe_alias}: so5Score(playerSlug: "{slug}") {{\n'
            f'  score\n'
            f'  player {{ displayName position }}\n'
            f'}}'
        )

    aliases_str = "\n".join(aliases)
    return f"""
query {{
  so5 {{
    so5Fixture(slug: "{fixture_slug}") {{
      games {{
        id
        so5ScoreData: id
        {aliases_str}
      }}
    }}
  }}
}}
"""


def fetch_scores_for_game(fixture_slug: str, game_id: str, players: list) -> list:
    """
    Returns list of dicts {player, position, score, game_id} for
    all players in the game who have a score.
    """
    if not players:
        return []

    query = build_score_query(fixture_slug, game_id, players)
    data = gql(query)

    results = []
    try:
        games = data["so5"]["so5Fixture"]["games"]
    except (KeyError, TypeError):
        return []

    # Find the game entry matching our game_id
    target_game = None
    for game_entry in games:
        if game_entry.get("so5ScoreData") == game_id or game_entry.get("id") == game_id:
            target_game = game_entry
            break

    if not target_game:
        return []

    for i, player in enumerate(players):
        slug = player["slug"]
        safe_alias = f"p{i}_{slug.replace('-', '_')}"
        score_data = target_game.get(safe_alias)
        if score_data and score_data.get("score") is not None:
            results.append({
                "player": score_data["player"]["displayName"],
                "position": score_data["player"].get("position", ""),
                "score": score_data["score"],
                "game_id": game_id,
                "slug": slug,
            })

    return results


# ---------------------------------------------------------------------------
# Twitter
# ---------------------------------------------------------------------------

def build_twitter_client() -> tweepy.Client:
    return tweepy.Client(
        consumer_key=TWITTER_API_KEY,
        consumer_secret=TWITTER_API_SECRET,
        access_token=TWITTER_ACCESS_TOKEN,
        access_token_secret=TWITTER_ACCESS_TOKEN_SECRET,
    )


def post_tweet(client: tweepy.Client, text: str) -> bool:
    try:
        response = client.create_tweet(text=text)
        tweet_id = response.data["id"]
        logger.info("Tweet posted (id=%s): %s", tweet_id, text)
        return True
    except tweepy.TweepyException as exc:
        logger.error("Failed to post tweet: %s", exc)
        return False


def build_tweet(entry: dict, fixture: dict, game: dict) -> str:
    player = entry["player"]
    score = entry["score"]
    home = game["homeTeam"]["name"]
    away = game["awayTeam"]["name"]
    gw = fixture.get("gameWeek", "")
    gw_label = f" | GW{gw}" if gw else ""
    return (
        f"SORARE ALERT: {player} just scored {score:.1f} pts{gw_label}! "
        f"{home} vs {away} #Sorare #Fantasy"
    )


# ---------------------------------------------------------------------------
# Polling loop
# ---------------------------------------------------------------------------

def run_poll(twitter_client: tweepy.Client, alerted_keys: set) -> None:
    logger.info("Polling Sorare API...")

    fixtures = fetch_recent_fixtures()
    if not fixtures:
        logger.warning("No fixtures found.")
        return

    # Use only the two most recent fixtures (current + last completed)
    for fixture in fixtures[:2]:
        fixture_slug = fixture["slug"]
        game_week = fixture.get("gameWeek")
        games = fixture.get("games", [])
        logger.info(
            "Checking fixture: %s (GW%s) — %d games",
            fixture_slug, game_week, len(games),
        )

        for game in games:
            game_id = game["id"]
            players = fetch_players_for_game(game_id)
            if not players:
                continue

            scores = fetch_scores_for_game(fixture_slug, game_id, players)

            for entry in scores:
                if entry["score"] < SCORE_THRESHOLD:
                    continue

                # Deduplicate alerts: (player_slug, fixture_slug)
                alert_key = (entry["slug"], fixture_slug)
                if alert_key in alerted_keys:
                    continue

                logger.info(
                    "Player %s scored %.1f — posting tweet!",
                    entry["player"], entry["score"],
                )
                tweet_text = build_tweet(entry, fixture, game)
                success = post_tweet(twitter_client, tweet_text)
                if success:
                    alerted_keys.add(alert_key)


def main() -> None:
    logger.info(
        "Sorare Score Monitor started. Threshold: %d pts | Interval: %d min.",
        SCORE_THRESHOLD,
        POLL_INTERVAL_SECONDS // 60,
    )
    twitter_client = build_twitter_client()
    alerted_keys: set = set()

    while True:
        try:
            run_poll(twitter_client, alerted_keys)
        except Exception as exc:
            logger.error("Unexpected error in poll cycle: %s", exc, exc_info=True)

        logger.info("Sleeping %d seconds until next poll...", POLL_INTERVAL_SECONDS)
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
