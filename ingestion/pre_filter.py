"""
Pre-filter layer — runs before LangGraph to cut noise.

Two jobs:
1. should_skip(text) — returns True for messages not worth extracting from
2. bundle_threads(messages) — groups thread replies into one block per thread
   so the pipeline processes a whole conversation once instead of message-by-message.
"""

import re

# Messages shorter than this are almost always noise
MIN_LENGTH = 25

# Common noise phrases — exact matches after stripping and lowercasing
NOISE_PHRASES = {
    "ok", "okay", "got it", "thanks", "thank you", "lol", "haha",
    "sure", "noted", "sounds good", "will do", "👍", "👌", "✅",
    "done", "yep", "yup", "nope", "yes", "no", "agreed", "nice",
    "+1", ":+1:", ":white_check_mark:", ":thumbsup:",
}

# Messages that are only emoji (unicode emoji or Slack :emoji: codes)
EMOJI_ONLY_PATTERN = re.compile(
    r'^[\s:a-z_]+:$'        # Slack :emoji: codes
    r'|^[\U0001F300-\U0001FFFF\s]+$',  # Unicode emoji
    re.IGNORECASE
)

# Bot message indicators in metadata
BOT_SUBTYPES = {"bot_message", "channel_join", "channel_leave", "channel_topic", "channel_purpose"}


def should_skip(text: str, metadata: dict = None) -> bool:
    """
    Returns True if this message is noise and should not enter the pipeline.
    """
    if not text or not text.strip():
        return True

    stripped = text.strip()

    # Too short to contain a useful fact
    if len(stripped) < MIN_LENGTH:
        return True

    # Common noise phrase
    if stripped.lower() in NOISE_PHRASES:
        return True

    # Pure emoji
    if EMOJI_ONLY_PATTERN.match(stripped):
        return True

    # Bot message
    if metadata:
        subtype = metadata.get("subtype", "")
        if subtype in BOT_SUBTYPES:
            return True
        user = metadata.get("user", "")
        if user.startswith("B"):  # Slack bot user IDs start with B
            return True

    return False


def bundle_threads(messages: list[dict]) -> list[dict]:
    """
    Groups Slack thread replies into single bundled messages.

    Messages with the same thread_ts are concatenated into one block.
    Top-level messages (no thread_ts, or thread_ts == ts) are kept as-is.

    Input: list of message dicts with keys: content, timestamp, permalink, metadata
    Output: same format but thread replies collapsed into single entries
    """
    threads: dict[str, list] = {}  # thread_ts -> list of messages
    standalone = []

    for msg in messages:
        meta = msg.get("metadata") or {}
        thread_ts = meta.get("thread_ts")
        ts = meta.get("ts")

        if thread_ts and thread_ts != ts:
            # This is a reply — group it under the thread
            threads.setdefault(thread_ts, []).append(msg)
        else:
            standalone.append(msg)

    result = list(standalone)

    # Bundle each thread into one message
    for thread_ts, replies in threads.items():
        combined_text = "\n---\n".join(r["content"] for r in replies)
        bundled = {
            "content": f"[Thread]\n{combined_text}",
            "timestamp": replies[0]["timestamp"],
            "permalink": replies[0].get("permalink"),
            "metadata": replies[0].get("metadata", {}),
        }
        result.append(bundled)

    return result