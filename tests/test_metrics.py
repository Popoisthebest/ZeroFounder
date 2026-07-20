from agents.metrics import sentiment


def test_sentiment_is_rule_based_and_unknown_is_preserved():
    result = sentiment(
        [
            {"title": "Very useful", "body": "thanks"},
            {"title": "Broken", "body": "error"},
            {"title": "Question", "body": "How does this work?"},
        ]
    )
    assert result == {"positive": 1, "negative": 1, "unknown": 1}
