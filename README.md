# sorare-score-alert

A Python script that polls the [Sorare](https://sorare.com) GraphQL API every 15 minutes and posts a tweet via Twitter/X when any player scores 50+ Sorare points in the current fixture.

## Setup

1. Clone the repository
2. Install dependencies:
   ```
   pip install requests tweepy
   ```
3. Set the following environment variables (or add them to a `.env` file):

   | Variable | Description |
   |---|---|
   | `TWITTER_API_KEY` | Twitter/X app API key (consumer key) |
   | `TWITTER_API_SECRET` | Twitter/X app API secret (consumer secret) |
   | `TWITTER_ACCESS_TOKEN` | OAuth access token |
   | `TWITTER_ACCESS_TOKEN_SECRET` | OAuth access token secret |
   | `SORARE_API_KEY` | Sorare API key (optional — leave blank for public access) |

4. Run the script:
   ```
   python3 main.py
   ```

## How it works

- Fetches the two most recent Sorare fixtures via the GraphQL API
- For each game, retrieves all participating players
- Queries each player's Sorare score using batched aliased GraphQL requests (one API call per game)
- Tweets when a player's score exceeds the threshold (default: 50, production: 100)
- Deduplicates alerts so the same player is never tweeted twice per fixture

## Tweet format

```
SORARE ALERT: Erling Haaland just scored 112.5 pts (GW676)! Manchester City vs Arsenal #Sorare #Fantasy
```

## Configuration

Edit `main.py` to adjust:

- `SCORE_THRESHOLD` — minimum score to trigger a tweet (default: 50 for testing, change to 100 for production)
- `POLL_INTERVAL_SECONDS` — how often to poll (default: 900 seconds / 15 minutes)
