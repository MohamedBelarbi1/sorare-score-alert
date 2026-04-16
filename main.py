"""
Sorare Score Alert Bot
======================
Ce script surveille l'API Sorare et poste automatiquement sur X (Twitter)
quand un joueur obtient un score >= TARGET_SCORE.

Stratégie API : requêtes légères sur les derniers games pour rester
sous la limite de complexité GraphQL de l'API publique Sorare (500 points).

CONFIGURATION :
  - Les clés API sont lues depuis les variables d'environnement Railway/Replit
  - Lance le script : python main.py
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
# CONFIG — Chargée depuis les variables d'environnement
# ============================================================

# Clés X (Twitter) — définies dans Railway > Variables
X_API_KEY             = os.environ.get("X_API_KEY", "")
X_API_SECRET          = os.environ.get("X_API_SECRET", "")
X_ACCESS_TOKEN        = os.environ.get("X_ACCESS_TOKEN", "")
X_ACCESS_TOKEN_SECRET = os.environ.get("X_ACCESS_TOKEN_SECRET", "")

# Clé API Sorare (optionnelle — monte la limite de complexité à 30 000)
# Pour en obtenir une : https://docs.sorare.com/docs/authentication
SORARE_API_KEY = os.environ.get("SORARE_API_KEY", "")

# Fréquence de vérification en secondes (900 = 15 minutes)
CHECK_INTERVAL = 900

# Score cible à détecter
TARGET_SCORE = int(os.environ.get("TARGET_SCORE", "100"))

# Fichier pour mémoriser les scores déjà tweetés (évite les doublons)
ALREADY_POSTED_FILE = "already_posted.json"

# URL de l'API Sorare
SORARE_GRAPHQL_URL = "https://api.sorare.com/graphql"

# ============================================================
# TEMPLATES DE TWEETS
# Variables disponibles : {player}, {score}, {competition}, {player_hashtag}
# ============================================================

TWEET_TEMPLATES = [
    "🔥 PERFECT SCORE! {player} just hit {score} on @SorareHQ in {competition}! 🌟 #Sorare #Score100 #{player_hashtag}",
    "💯 {player} scores PERFECT {score} on @SorareHQ! Incredible performance in {competition}! 🚀 #Sorare #PerfectScore",
    "🎯 Perfect 100 alert! {player} just delivered a flawless performance on @SorareHQ ({competition}) ⚽ #Sorare #Score100",
    "⭐ {player} = absolute GOAT mode! {score}/100 on @SorareHQ in {competition} 🤩 #Sorare #{player_hashtag}",
]

# ============================================================
# FONCTIONS UTILITAIRES
# ============================================================

def load_already_posted():
    """Charge la liste des scores déjà tweetés pour éviter les doublons."""
    if os.path.exists(ALREADY_POSTED_FILE):
        with open(ALREADY_POSTED_FILE, "r") as f:
            return set(json.load(f))
    return set()

def save_already_posted(posted_set):
    """Sauvegarde la liste des scores déjà tweetés."""
    with open(ALREADY_POSTED_FILE, "w") as f:
        json.dump(list(posted_set), f, indent=2)

def make_player_hashtag(player_name):
    """Transforme un nom de joueur en hashtag (ex: 'Kylian Mbappé' -> 'KylianMbappe')."""
    name = unicodedata.normalize('NFD', player_name)
    name = ''.join(c for c in name if unicodedata.category(c) != 'Mn')
    return name.replace(' ', '').replace('-', '').replace("'", "")

def build_headers():
    """Construit les headers HTTP pour l'API Sorare."""
    headers = {"Content-Type": "application/json"}
    if SORARE_API_KEY:
        headers["APIKEY"] = SORARE_API_KEY
        print("   🔑 Utilisation de la clé API Sorare (limite 30 000)")
    else:
        print("   ⚠️  Pas de clé API Sorare (limite 500 — requêtes simplifiées)")
    return headers

# ============================================================
# CONNEXION À L'API X (TWITTER)
# ============================================================

def get_twitter_client():
    """Initialise le client X (Twitter) avec tes clés."""
    if not all([X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_TOKEN_SECRET]):
        print("❌ Clés X (Twitter) manquantes ! Vérifie tes variables d'environnement Railway.")
        return None
    client = tweepy.Client(
        consumer_key=X_API_KEY,
        consumer_secret=X_API_SECRET,
        access_token=X_ACCESS_TOKEN,
        access_token_secret=X_ACCESS_TOKEN_SECRET
    )
    return client

def post_tweet(client, message):
    """Poste un tweet."""
    if client is None:
        print("   ❌ Client Twitter non initialisé, tweet ignoré")
        return False
    try:
        response = client.create_tweet(text=message)
        print(f"   ✅ Tweet posté ! ID: {response.data['id']}")
        return True
    except tweepy.TweepyException as e:
        print(f"   ❌ Erreur Twitter : {e}")
        return False

# ============================================================
# RÉCUPÉRATION DES SCORES VIA L'API SORARE
# Stratégie : requête légère sur les fixtures récentes puis
# requête séparée par fixture pour les scores (évite explosion complexité)
# ============================================================

def fetch_current_fixture_slug(headers):
    """
    Récupère le slug de la fixture la plus récente.
    Requête très légère (~30 points de complexité).
    """
    query = """
    query GetCurrentFixture {
      so5Fixtures(first: 1) {
        nodes {
          slug
          gameWeek
          startDate
          endDate
        }
      }
    }
    """
    try:
        resp = requests.post(
            SORARE_GRAPHQL_URL,
            json={"query": query},
            headers=headers,
            timeout=30
        )
        data = resp.json()

        if "errors" in data:
            for err in data["errors"]:
                print(f"   ⚠️  Erreur GraphQL : {err.get('message', err)}")
            return None

        nodes = data.get("data", {}).get("so5Fixtures", {}).get("nodes", [])
        if nodes:
            fixture = nodes[0]
            print(f"   Fixture en cours : {fixture['slug']} (GW{fixture['gameWeek']})")
            return fixture["slug"]
        return None

    except Exception as e:
        print(f"   ❌ Erreur réseau : {e}")
        return None


def fetch_scores_for_fixture(fixture_slug, headers):
    """
    Récupère les scores pour une fixture donnée.
    On pagine sur les leaderboards avec first:3 pour rester sous 500 points.
    """
    # Requête simplifiée : seulement 3 leaderboards, 20 rankings max
    query = """
    query GetFixtureScores($slug: String!) {
      so5Fixture(slug: $slug) {
        slug
        so5Leaderboards(first: 3) {
          nodes {
            displayName
            so5Rankings(first: 20) {
              nodes {
                so5Lineup {
                  so5AppearanceProjections {
                    score
                    player {
                      displayName
                    }
                  }
                }
              }
            }
          }
        }
      }
    }
    """
    try:
        resp = requests.post(
            SORARE_GRAPHQL_URL,
            json={"query": query, "variables": {"slug": fixture_slug}},
            headers=headers,
            timeout=30
        )

        if resp.status_code == 429:
            print("   ⏳ Rate limit Sorare (429) — on attend 60 secondes...")
            time.sleep(60)
            return []

        data = resp.json()

        if "errors" in data:
            for err in data["errors"]:
                msg = err.get('message', str(err))
                print(f"   ⚠️  Erreur GraphQL : {msg}")
                if "complexity" in msg.lower():
                    print("   💡 Conseil : ajoute une SORARE_API_KEY dans Railway pour lever la limite")
            return []

        scores = []
        fixture_data = data.get("data", {}).get("so5Fixture", {})
        if not fixture_data:
            return []

        leaderboards = fixture_data.get("so5Leaderboards", {}).get("nodes", [])
        for lb in leaderboards:
            competition_name = lb.get("displayName", "Unknown Competition")
            rankings = lb.get("so5Rankings", {}).get("nodes", [])

            for ranking in rankings:
                lineup = ranking.get("so5Lineup")
                if not lineup:
                    continue
                projections = lineup.get("so5AppearanceProjections", [])
                for proj in projections:
                    player_score = proj.get("score")
                    player_name = proj.get("player", {}).get("displayName", "Unknown")

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
    """Point d'entrée principal pour récupérer les scores."""
    headers = build_headers()

    fixture_slug = fetch_current_fixture_slug(headers)
    if not fixture_slug:
        print("   ⚠️  Impossible de récupérer la fixture en cours")
        return []

    # Petite pause entre les deux requêtes pour éviter le rate-limit
    time.sleep(3)

    scores = fetch_scores_for_fixture(fixture_slug, headers)
    return scores


# ============================================================
# BOUCLE PRINCIPALE
# ============================================================

def run_bot():
    """Lance le bot en boucle infinie."""
    print("=" * 55)
    print("🤖  Sorare Score Alert Bot — démarré !")
    print(f"    Score cible     : {TARGET_SCORE}")
    print(f"    Intervalle      : {CHECK_INTERVAL // 60} minutes")
    print(f"    Clé Sorare      : {'✅ configurée' if SORARE_API_KEY else '❌ absente (limite 500)'}")
    print(f"    Clés Twitter    : {'✅ configurées' if X_API_KEY else '❌ absentes'}")
    print("=" * 55)
    print()

    twitter_client = get_twitter_client()
    already_posted = load_already_posted()
    iteration = 0

    while True:
        iteration += 1
        now = datetime.now().strftime('%H:%M:%S')
        print(f"[{now}] ── Check #{iteration} ──────────────────────────")

        scores = fetch_recent_scores()

        new_scores = [s for s in scores if s["score_id"] not in already_posted]

        if not new_scores:
            if scores:
                print(f"   {len(scores)} score(s) ≥ {TARGET_SCORE} trouvé(s) mais déjà tweeté(s)")
            else:
                print(f"   Aucun score ≥ {TARGET_SCORE} trouvé pour le moment")
        else:
            print(f"   🎉 {len(new_scores)} nouveau(x) score(s) parfait(s) !")

        for score_data in new_scores:
            template = random.choice(TWEET_TEMPLATES)
            tweet_text = template.format(
                player=score_data["player_name"],
                score=int(score_data["score"]),
                competition=score_data["competition"],
                player_hashtag=make_player_hashtag(score_data["player_name"])
            )

            print(f"   → Tweet pour {score_data['player_name']} ({score_data['score']})")
            success = post_tweet(twitter_client, tweet_text)

            if success:
                already_posted.add(score_data["score_id"])
                save_already_posted(already_posted)

            # Pause entre tweets pour ne pas spammer
            time.sleep(5)

        print(f"   Prochaine vérification dans {CHECK_INTERVAL // 60} min...")
        time.sleep(CHECK_INTERVAL)


# ============================================================
# POINT D'ENTRÉE
# ============================================================

if __name__ == "__main__":
    run_bot()
