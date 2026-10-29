"""Conservative issuer aliases shared by discovery and original-title checks."""

# Confirmed ordinary-language collisions in production RSS. Full legal names
# and explicit cashtags remain usable. This is not an exhaustive ambiguity list.
AMBIGUOUS_STANDALONE = frozenset({'team', 'people', 'confidence in', 'sea', 'visa', 'axt', 'the eastern', 'academy', 'pool', 'news', 'dow', 'nasdaq', 'msci', 'keep'})


def specific_alias(alias, *, provider=None, code=None):
    if not isinstance(alias, str) or not alias.strip():return False
    # Bare "target" is a market term. The exact corporate RSS host owns this alias.
    if alias.strip().casefold()=='target':return provider=='target_ir' and code=='US.TGT'
    return alias.strip().casefold() not in AMBIGUOUS_STANDALONE
