"""
Sorare Score Alert Bot
======================
Surveille l'API Sorare et poste sur X (Twitter) quand un joueur
obtient un score >= TARGET_SCORE sur Sorare.
"""

import requests
import tweepy
import json
import time
import os
import random
import unicodedata
from datetime import datetime

# ============================================================
# CONFIG — Variables d'environnement Railway
# ============================================================

# Clés X (Twitter) — accepte les deux formats de noms
X_API_KEY             = os.environ.get("X_API_KEY") or os.environ.get("TWITTER_API_KEY", "")
X_API_SECRET          = os.environ.get("X_API_SECRET") or os.environ.get("TWITTER_API_SECRET", "")
X_ACCESS_TOKEN        = os.environ.get("X_ACCESS_TOKEN") or os.environ.get("TWITTER_ACCESS_TOKEN", "")
X_ACCESS_TOKEN_SECRET = os.environ.get("X_ACCESS_TOKEN_SECRET") or os.environ.get("TWITTER_ACCESS_TOKEN_SECRET", "")

# Clé API Sorare
SORARE_API_KEY = os.environ.get("SORARE_API_KEY", "")

# Score cible
TARGET_SCORE = int(os.environ.get("TARGET_SCORE", "100"))

# Intervalle entre deux checks (secondes)
CHECK_INTERVAL = 900

# Fichier anti-doublons
ALREADY_POSTED_FILE = "already_posted.json"

# URL GraphQL Sorare
SORARE_GRAPHQL_URL = "https://api.sorare.com/graphql"

# ============================================================
# TEMPLATES DE TWEETS
# ============================================================

TWEET_TEMPLATES = [
    "🔥 PERFECT SCORE! {player} just hit {score} on @SorareHQ! 🌟 #Sorare #Score100 #{player_hashtag}",
    "💯 {player} scores PERFECT {score} on @SorareHQ! Incredible performance! 🚀 #Sorare #PerfectScore",
    "🎯 Perfect 100 alert! {player} just delivered a flawless performance on @SorareHQ ⚽ #Sorare #Score100",
    "⭐ {player} = GOAT mode! {score}/100 on @SorareHQ 🤩 #Sorare #{player_hashtag}",
]

# ============================================================
# UTILITAIRES
# ============================================================

def load_already_posted():
    if os.path.exists(ALREADY_POSTED_FILE):
        with open(ALREADY_POSTED_FILE, "r") as f:
            return set(json.load(f))
    return set()

def save_already_posted(posted_set):
    with open(ALREADY_POSTED_FILE, "w") as f:
        json.dump(list(posted_set), f, indent=2)

def make_player_hashtag(player_name):
    name = unicodedata.normalize('NFD', player_name)
    name = ''.join(c for c in name if unicodedata.category(c) != 'Mn')
    return name.replace(' ', '').replace('-', '').replace("'", "")

def build_headers():
    headers = {"Content-Type": "application/json"}
    if SORARE_API_KEY:
        headers["APIKEY"] = SORARE_API_KEY
    return headers

# ============================================================
# TWITTER
# ============================================================

def get_twitter_client():
    if not all([X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_TOKEN_SECRET]):
        print("❌ Clés Twitter manquantes dans les variables d'environnement Railway")
        return None
    return tweepy.Client(
        consumer_key=X_API_KEY,
        consumer_secret=X_API_SECRET,
        access_token=X_ACCESS_TOKEN,
        access_token_secret=X_ACCESS_TOKEN_SECRET
    )

def post_tweet(client, message):
    if not client:
        return False
    try:
        response = client.create_tweet(text=message)
        print(f"   ✅ Tweet posté ! ID: {response.data['id']}")
        return True
    except tweepy.TweepyException as e:
        print(f"   ❌ Erreur Twitter : {e}")
        return False

# ============================================================
# SORARE API
# ============================================================

def fetch_current_fixture_slug(headers):
    """
    Récupère le slug de la fixture actuelle via currentSo5Fixture.
    On utilise 'currentSo5Fixture' qui est la query racine correcte.
    """
    # On tente d'abord currentSo5Fixture, puis on essaie d'autres variantes
    queries_to_try = [
        # Tentative 1 : currentSo5Fixture (fixture en cours)
        ("currentSo5Fixture", """
        query {
          currentSo5Fixture {
            slug
            gameWeek
            displayName
          }
        }
        """),
        # Tentative 2 : so5Fixture avec "current"
        ("so5Fixture_next", """
        query {
          so5Fixture(slug: "current") {
            slug
            gameWeek
            displayName
          }
        }
        """),
    ]

    for name, query in queries_to_try:
        try:
            resp = requests.post(
                SORARE_GRAPHQL_URL,
                json={"query": query},
                headers=headers,
                timeout=30
            )

            if resp.status_code == 429:
                print("   ⏳ Rate limit (429) — attente 60s...")
                time.sleep(60)
                return None

            data = resp.json()

            if "errors" in data:
                errs = data["errors"]
                for e in errs:
                    print(f"   ⚠️  [{name}] {e.get('message', e)}")
                continue

            # Cherche dans les données
            d = data.get("data", {})
            fixture = d.get("currentSo5Fixture") or d.get("so5Fixture")
            if fixture and fixture.get("slug"):
                slug = fixture["slug"]
                gw = fixture.get("gameWeek", "?")
                name_display = fixture.get("displayName", slug)
                print(f"   📅 Fixture : {name_display} (GW{gw}) — slug: {slug}")
                return slug

        except Exception as e:
            print(f"   ❌ Erreur réseau [{name}] : {e}")
            continue

    print("   ⚠️  Impossible de récupérer la fixture en cours")
    return None


def fetch_scores_for_fixture(fixture_slug, headers):
    """
    Récupère les scores >= TARGET_SCORE pour une fixture donnée.
    Utilise orderedSo5ScoresByPosition avec minScore.
    """
    query = """
    query GetFixtureScores($slug: String!, $minScore: Int!) {
      so5Fixture(slug: $slug) {
        slug
        displayName
        orderedSo5ScoresByPosition(first: 50, minScore: $minScore) {
          nodes {
            score
            player {
              displayName
            }
            so5Fixture {
              displayName
            }
          }
        }
      }
    }
    """

    try:
        resp = requests.post(
            SORARE_GRAPHQL_URL,
            json={
                "query": query,
                "variables": {
                    "slug": fixture_slug,
                    "minScore": TARGET_SCORE
                }
            },
            headers=headers,
            timeout=30
        )

        if resp.status_code == 429:
            print("   ⏳ Rate limit (429) — attente 60s...")
            time.sleep(60)
            return []

        data = resp.json()

        if "errors" in data:
            for err in data["errors"]:
                msg = err.get('message', str(err))
                print(f"   ⚠️  Erreur GraphQL : {msg}")
            return []

        scores = []
        fixture_data = data.get("data", {}).get("so5Fixture", {})
        if not fixture_data:
            return []

        competition_name = fixture_data.get("displayName", "Sorare")
        nodes = fixture_data.get("orderedSo5ScoresByPosition", {}).get("nodes", [])

        for node in nodes:
            player_score = node.get("score")
            player_name = node.get("player", {}).get("displayName", "Unknown")

            if player_score is not None and float(player_score) >= TARGET_SCORE:
                score_id = f"{fixture_slug}_{player_name}_{player_score}"
                scores.append({
                    "player_name": player_name,
                    "score": float(player_score),
                    "competition": competition_name,
                    "fixture_slug": fixture_slug,
                    "score_id": score_id,
                })

        return scores

    except Exception as e:
        print(f"   ❌ Erreur réseau : {e}")
        return []


def fetch_recent_scores():
    headers = build_headers()
    key_status = "✅ clé API Sorare active" if SORARE_API_KEY else "⚠️  pas de clé API (limite 500)"
    print(f"   {key_status}")

    fixture_slug = fetch_current_fixture_slug(headers)
    if not fixture_slug:
        return []

    time.sleep(3)  # pause anti rate-limit entre les deux requêtes

    scores = fetch_scores_for_fixture(fixture_slug, headers)
    print(f"   {len(scores)} score(s) >= {TARGET_SCORE} trouvé(s)")
    return scores


# ============================================================
# BOUCLE PRINCIPALE
# ============================================================

def run_bot():
    print("=" * 55)
    print("🤖  Sorare Score Alert Bot — démarré !")
    print(f"    Score cible  : {TARGET_SCORE}")
    print(f"    Intervalle   : {CHECK_INTERVAL // 60} minutes")
    print(f"    Clé Sorare   : {'✅ configurée' if SORARE_API_KEY else '❌ absente'}")
    print(f"    Clés Twitter : {'✅ configurées' if X_API_KEY else '❌ absentes'}")
    print("=" * 55)

    twitter_client = get_twitter_client()
    already_posted = load_already_posted()
    iteration = 0

    while True:
        iteration += 1
        now = datetime.now().strftime('%H:%M:%S')
        print(f"\n[{now}] ── Check #{iteration} ──────────────────────")

        scores = fetch_recent_scores()
        new_scores = [s for s in scores if s["score_id"] not in already_posted]

        if not new_scores:
            if scores:
                print(f"   Tous les scores déjà tweetés")
        else:
            print(f"   🎉 {len(new_scores)} nouveau(x) score(s) à tweeter !")

        for score_data in new_scores:
            template = random.choice(TWEET_TEMPLATES)
            tweet_text = template.format(
                player=score_data["player_name"],
                score=int(score_data["score"]),
                competition=score_data["competition"],
                player_hashtag=make_player_hashtag(score_data["player_name"])
            )

            print(f"   → {score_data['player_name']} ({score_data['score']})")
            success = post_tweet(twitter_client, tweet_text)

            if success:
                already_posted.add(score_data["score_id"])
                save_already_posted(already_posted)

            time.sleep(5)

        print(f"   💤 Prochain check dans {CHECK_INTERVAL // 60} min...")
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    run_bot()
