import re

stop_words = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has",
    "he", "in", "is", "it", "its", "of", "on", "that", "the", "to", "was",
    "were", "will", "with",
}


def clean_text(text):
    """
    Clean and preprocess complaint text without eager network calls or unclosed NLTK DB handles.
    """
    if not isinstance(text, str):
        return ""

    text = text.lower()
    text = re.sub(r"[^a-zA-Z\s]", "", text)
    words = text.split()
    return " ".join(w for w in words if w not in stop_words)