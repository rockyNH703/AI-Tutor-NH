"""Mask obvious secrets before a model or an embeddings provider reads a text.

Engineers paste router and firewall configs into the chat, and configs carry passwords,
keys and community strings. The stored message keeps what the student wrote (only they
see it); what leaves for the LLM, its fallback or the embeddings provider has each secret
VALUE replaced by `<redacted>`. The keyword and type stay (`enable secret 9 <redacted>`),
so the model still reads the config correctly.

A pattern list masks obvious secrets, not every secret. A password in prose ("mine is
hunter2"), a vendor syntax not listed here, or a bare random string goes through
unchanged. The patterns aim for precision as well, so the engineer still gets help:
addresses, interfaces, ACLs, commands without a value and prose about passwords stay
as they are.

Every pattern starts with its keyword (a literal, or a choice of a few), so `re` jumps
from one occurrence to the next, and a rule runs only when its keyword is in the text.
Every repeat has a bound and every value is atomic, so nothing backtracks
catastrophically: a pasted 2 MB config takes a fraction of a second. Nothing is
truncated, because a skipped tail would go out unmasked. Masking is idempotent: a value
that is already `<redacted>` is neither masked nor counted again.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Callable
from typing import Any

from langchain_core.embeddings import Embeddings

from app.llm.base import ChatMessage, GenerationOptions, LLMProvider, LLMStream
from app.logging import get_logger

log = get_logger("app.llm")

PLACEHOLDER = "<redacted>"
PRIVATE_KEY_PLACEHOLDER = "<redacted private key>"

# ------------------------------------------------------------------- building blocks
_SP = r"[ \t]+"
# A value: quoted (the quotes stay and the inside is masked), or one bare token.
_VALUE = r"""(?>"[^"\n]{1,512}"|'[^'\n]{1,512}'|[^\s"'`][^\s"`]{0,511})"""
# Junos statements end at `;` in the curly format.
_JUNOS_VALUE = r"""(?>"[^"\n]{1,512}"|[^\s"';`][^\s";`]{0,511})"""
# In `NAME=VALUE` lines a bare value stops before code punctuation (`f(password=pw)`).
_PLAIN_VALUE = r"""(?>"[^"\n]{1,512}"|'[^'\n]{1,512}'|[^\s"'`,;{}()\[\]<>][^\s"'`,;{}()\[\]<>]{0,511})"""
_EOL = r"[ \t]*(?:`|\r?$)"
_END = rf"(?={_EOL})"
# Cisco's type before a value: 0 (clear), 5/8/9 (hashes), 6/7 (reversible), or a word (IOS XR).
_TYPE = r"(?:[0-9]|encrypted|clear)"
# What may come before a CLI command on its line: indentation, a Markdown list or quote marker,
# and a prompt (`R1(config)#`, `user@fw> `).
_COMMAND_PREFIX = re.compile(
    r"[ \t]*(?:[>*+\-][ \t]+|\d{1,3}[.)][ \t]+)?(?:[\w.\-/:@]{1,80}(?:\([\w.\-/]{1,40}\))?[#>][ \t]*)?"
)
_WORD_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")


def _starts_command(text: str, position: int) -> bool:
    """Does a CLI command start here: at a line start (after `_COMMAND_PREFIX`) or inside inline code?"""
    if position and text[position - 1] == "`":
        return True
    floor = max(0, position - 160)  # no prefix is longer; a longer one is prose
    newline = text.rfind("\n", floor, position)
    if newline < 0 and floor > 0:
        return False
    return _COMMAND_PREFIX.fullmatch(text, newline + 1, position) is not None


_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")
# Accepts or refuses one value: (the text, the match, the value).
_Accept = Callable[[str, re.Match[str], str], bool]


class _Rule:
    """One pattern; its groups named `v1`, `v2`, ... are the values that get masked.

    A template starting with `{LEAD}` matches only where a command starts (`_starts_command`),
    one starting with `{B}` only at the start of a word. Both are checked on each match,
    not in the pattern, so the pattern starts with its keyword and `re` searches for it
    quickly. For the same reason a `fold` rule is written in lowercase and searches a
    lowercased copy of the text: IGNORECASE would make `re` try every position.
    """

    def __init__(self, template: str, hints: tuple[str, ...], *, fold: bool = False, accept: _Accept | None = None):
        self.lead = template.startswith("{LEAD}")
        self.boundary = template.startswith("{B}")
        pattern = (
            template.removeprefix("{LEAD}")
            .removeprefix("{B}")
            .replace("{SP}", _SP)
            .replace("{END}", _END)
            .replace("{EOL}", _EOL)
            .replace("{T}", _TYPE)
        )
        numbers = iter(range(1, 100))
        values = {"V": _VALUE, "J": _JUNOS_VALUE, "P": _PLAIN_VALUE}
        pattern = re.sub(r"\{([VJP])\}", lambda m: f"(?P<v{next(numbers)}>{values[m.group(1)]})", pattern)
        self.pattern = re.compile(pattern, re.MULTILINE)
        self.groups = [index for name, index in self.pattern.groupindex.items() if name.startswith("v")]
        self.hints = hints  # lowercase; the rule runs only when one of them is in the text
        self.fold = fold
        self.accept = accept

    def apply(self, text: str) -> tuple[str, int]:
        haystack = text
        if self.fold:
            haystack = text.lower()
            if len(haystack) != len(text):  # a few non-ASCII letters lowercase to two characters
                haystack = text.translate(_ASCII_LOWER)
        pieces: list[str] = []
        last = 0
        for match in self.pattern.finditer(haystack):  # its offsets are the text's
            start = match.start()
            if self.lead and not _starts_command(text, start):
                continue
            if self.boundary and start and text[start - 1] in _WORD_CHARS:
                continue
            for begin, end in sorted(match.span(index) for index in self.groups if match.start(index) >= 0):
                value = text[begin:end]
                masked = _masked(value)
                if masked is None or (self.accept is not None and not self.accept(text, match, value)):
                    continue
                pieces += [text[last:begin], masked]
                last = end
        if not pieces:
            return text, 0
        count = len(pieces) // 2
        pieces.append(text[last:])
        return "".join(pieces), count


def _is_placeholder(value: str) -> bool:
    """Already masked, or not a real value: `<redacted>`, `<your-password>`, `*****`, empty."""
    return "<redacted" in value or (value.startswith("<") and value.endswith(">")) or set(value) <= set("*xX.…")


def _masked(value: str) -> str | None:
    """The masked form of `value`, or None when there is nothing to mask."""
    quote = value[0]
    if quote in "\"'" and len(value) > 1 and value[-1] == quote:
        return None if _is_placeholder(value[1:-1]) else f"{quote}{PLACEHOLDER}{quote}"
    return None if _is_placeholder(value) else PLACEHOLDER


# ---------------------------------------------------- NAME=VALUE: env files, YAML, JSON, flags
# fmt: off
_KEY_PARTS = {
    "password", "passwd", "passphrase", "pwd", "secret", "token", "credential", "credentials", "psk",
    "apikey", "privatekey", "accesskey", "secretkey", "authkey",
}
_KEY_PAIRS = {
    (word, "key")
    for word in ("api", "private", "access", "secret", "shared", "auth", "signing", "encryption", "master")
}
# A name with one of these parts holds a setting about a secret, never the secret itself.
_NOT_SECRET_PARTS = {
    "policy", "length", "len", "min", "max", "count", "expiry", "expires", "expiration", "ttl", "timeout",
    "file", "path", "url", "uri", "endpoint", "type", "name", "hint", "enable", "enabled", "required", "reset",
    "limit", "age", "history", "rotation", "encoding", "algorithm", "header", "field", "prompt", "dir", "format",
    "regex", "pattern", "strength", "complexity", "lifetime", "duration", "interval", "id", "size", "mode",
    "method", "label", "title", "version", "prefix", "scope", "manager", "store", "provider", "ref", "env",
    "location", "placeholder", "tokens", "usage", "kind", "source", "mask", "masked",
}
_NOT_A_VALUE = {
    "true", "false", "yes", "no", "on", "off", "none", "null", "nil", "undefined", "enabled", "disabled",
    "required", "optional", "str", "string", "int", "bool", "bytes", "secretstr", "any", "object",
}
# fmt: on
_NAME_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-")
_NAME_PART = re.compile(r"[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])")
_IDENTIFIER = re.compile(r"[A-Za-z_]\w*")
# A template stands in for the value: `${DB_PASSWORD}`, `$(cat f)`, `{{ vault }}`, `%(pw)s`, `$TOKEN`.
_TEMPLATE = re.compile(r"\$\{|\$\(|\{\{|%\(|^\$[A-Za-z_]\w*$")
# In an assignment, so does an attribute path (`settings.secret_key`) or a URL.
_REFERENCE = re.compile(rf"{_TEMPLATE.pattern}|^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+$|^[a-z]+://")


def _name_parts(name: str) -> list[str]:
    return [part.lower() for part in _NAME_PART.findall(name)]


def _unquoted(value: str) -> str:
    return value[1:-1] if value[:1] in "\"'" and len(value) > 1 else value


def _secret_assignment(text: str, match: re.Match[str], value: str) -> bool:
    """Is this `NAME = VALUE` a secret? The name says so, and the value is not code or a setting."""
    start = match.start()
    while start > 0 and match.start() - start < 60 and text[start - 1] in _NAME_CHARS:
        start -= 1
    name = text[start : match.end("n")]
    parts = _name_parts(name)
    if not (_KEY_PARTS.intersection(parts) or _KEY_PAIRS.intersection(zip(parts, parts[1:], strict=False))):
        return False
    if _NOT_SECRET_PARTS.intersection(parts) or _REFERENCE.search(_unquoted(value)):
        return False
    if value[0] in "\"'":
        return True
    if value.lower() in _NOT_A_VALUE:
        return False
    # Code, not config: `password = password`, `token = access_token`, `key = apiKey`.
    # An env-style name (DB_PASSWORD=password) is config, whatever its value.
    if _IDENTIFIER.fullmatch(value) and not name.isupper():
        words = _name_parts(value)
        if value.lower() in parts or (len(words) > 1 and (_KEY_PARTS.intersection(words) or words[-1] == "key")):
            return False
    return True


def _not_template(text: str, match: re.Match[str], value: str) -> bool:
    return _TEMPLATE.search(_unquoted(value)) is None


# ------------------------------------------------------------------------------- rules
# Order matters only for counting: a value masked by an earlier rule reads as `<redacted>`
# to every later one, which leaves it alone.
_RULES = (
    # Secrets by their shape: the whole token goes.
    _Rule(r"{B}(?P<v1>A(?:KIA|SIA)[A-Z0-9]{16})(?![A-Za-z0-9])", ("akia", "asia")),
    _Rule(r"{B}(?P<v1>eyJ[A-Za-z0-9_\-]{5,4096}+\.eyJ[A-Za-z0-9_\-]{2,8192}+\.[A-Za-z0-9_\-]{0,4096}+)", ("eyj",)),
    _Rule(
        r"{B}(?P<v1>gh[pousr]_[A-Za-z0-9]{36,255}+|github_pat_[A-Za-z0-9_]{22,255}+)",
        ("ghp_", "gho_", "ghu_", "ghs_", "ghr_", "github_pat_"),
    ),
    _Rule(r"{B}(?P<v1>glpat-[A-Za-z0-9_\-]{20,255}+)", ("glpat-",)),
    _Rule(r"{B}(?P<v1>xox[abprs]-[A-Za-z0-9\-]{10,255}+)", ("xox",)),
    _Rule(r"{B}(?P<v1>GOCSPX-[A-Za-z0-9_\-]{20,255}+)", ("gocspx-",)),
    _Rule(r"{B}(?P<v1>AIza[A-Za-z0-9_\-]{35})", ("aiza",)),
    _Rule(r"{B}(?P<v1>[sr]k_(?:live|test)_[A-Za-z0-9]{16,255}+)", ("k_live_", "k_test_")),
    _Rule(r"{B}(?P<v1>sk-(?=[A-Za-z0-9_\-]{0,255}[0-9])[A-Za-z0-9_\-]{20,255}+)", ("sk-",)),
    # HTTP credentials.
    _Rule(
        r"authorization[\"']?[ \t]*[:=][ \t]*[\"']?(?:bearer|basic|token|negotiate){SP}{V}",
        ("authorization",),
        fold=True,
        accept=_not_template,
    ),
    _Rule(r"{B}bearer{SP}(?P<v1>(?=[a-z0-9\-._~+/]{0,4096}[0-9])[a-z0-9\-._~+/]{16,4096}+=*)", ("bearer",), fold=True),
    # scheme://user:PASSWORD@host
    _Rule(r"://[^\s:/@\"'`<>]{1,256}+:(?P<v1>[^\s/@\"'`]{1,512}+)@", ("://",), accept=_not_template),
    # ---- Cisco IOS, IOS-XE, IOS-XR, NX-OS, ASA (lowercase keywords, as the CLI writes them)
    _Rule(
        r"{LEAD}enable{SP}(?:algorithm-type{SP}[\w\-]{1,20}{SP})?(?:password|secret){SP}(?:level{SP}\d{1,2}{SP})?"
        r"(?:{T}{SP}{V}|{V}(?:{END}|(?={SP}(?:encrypted|pbkdf2|level)\b)))",
        ("enable",),
    ),
    _Rule(
        r"{LEAD}username{SP}\S{1,64}(?:{SP}\S{1,64}){0,8}?{SP}(?:password|secret){SP}(?:{T}{SP}{V}|{V})", ("username",)
    ),
    _Rule(r"{LEAD}password{SP}(?:{T}{SP}{V}|{V}{END})", ("password",)),
    _Rule(r"{LEAD}passwd{SP}{V}(?=(?:{SP}encrypted)?{EOL})", ("passwd",)),
    # `key` in a radius/tacacs server or ASA aaa-server block; never `key chain X` or `key 1`.
    _Rule(
        r"{LEAD}key{SP}(?:config-key{SP}password-encrypt{SP}{V}|{T}{SP}{V}"
        r"|(?!chain\b|config-key\b|[0-9]{1,10}{EOL}){V}){END}",
        ("key",),
    ),
    _Rule(r"{B}key-string{SP}(?:{T}{SP}{V}|{V})(?:{END}|(?={SP}timeout\b))", ("key-string",)),
    _Rule(
        r"{LEAD}snmp-server{SP}(?:community{SP}{V}|host{SP}[^\n]{0,200}?[ \t]community{SP}{V}"
        r"|host{SP}(?:\d{1,3}(?:\.\d{1,3}){3}|[0-9A-Fa-f:]{2,39}|[A-Za-z][\w.\-]{0,62})(?:{SP}vrf{SP}\S{1,32})?"
        r"(?:{SP}(?:traps|informs))?(?:{SP}version{SP}(?:1|2c))?{SP}"
        r"(?!(?:community|version|traps|informs|vrf|udp-port|use-vrf)\b|\d{1,3}(?:\.\d{1,3}){3}(?![\w.])){V})",
        ("snmp-server",),
    ),
    _Rule(
        r"{LEAD}snmp-server{SP}user{SP}[^\n]{0,200}?[ \t]auth{SP}(?:md5|sha(?:-?\d{3})?){SP}{V}"
        r"(?:{SP}priv{SP}(?:(?:3des|des|aes(?:-?(?:128|192|256))?)(?:{SP}(?:128|192|256))?{SP})?{V})?",
        ("snmp-server",),
    ),
    _Rule(r"crypto{SP}isakmp{SP}key{SP}(?:[0-9]{SP})?{V}(?={SP}(?:address|hostname)\b)", ("isakmp",)),
    _Rule(
        r"{B}pre-shared-key{SP}(?:(?:address|hostname){SP}\S{1,64}(?:{SP}\S{1,64})?{SP}key{SP}(?:[0-9]{SP})?{V}"
        r"|(?:local|remote){SP}(?:[0-9]{SP})?{V}|key{SP}{V}|[0-9]{SP}{V}"
        r"|(?!(?:ascii-text|hexadecimal|address|hostname|local|remote|key)\b){J}(?:{END}|(?=[ \t]*;)))",
        ("pre-shared-key",),
    ),
    _Rule(
        r"{B}(?:tacacs|radius)-server{SP}(?:[^\n]{0,160}?[ \t])?key{SP}(?:{T}{SP}{V}|{V}(?:{END}"
        r"|(?={SP}(?:auth-port|acct-port|port|timeout|retransmit|single-connection|authentication|accounting)\b)))",
        ("tacacs-server", "radius-server"),
    ),
    # OSPF/NTP/BGP/IS-IS/PPP. NTP's `authentication-key 1 md5 <x> 7` names the key number first.
    _Rule(
        r"{B}authentication-key{SP}(?:\d{1,10}{SP}(?:md5|sha1|sha256|hmac-sha[\w\-]{0,8}|cmac-aes-128"
        r"|type{SP}[\w\-]{1,20}{SP}value){SP}{V}|{T}{SP}{V}|{J}(?:{END}|(?=[ \t]*;)))",
        ("authentication-key",),
    ),
    _Rule(r"{B}message-digest-key{SP}\d{1,10}{SP}md5{SP}(?:{T}{SP}{V}|{V})", ("message-digest-key",)),
    _Rule(r"{LEAD}neighbor{SP}\S{1,64}{SP}password{SP}(?:{T}{SP}{V}|{V})", ("neighbor",)),
    _Rule(r"{LEAD}isis{SP}password{SP}{V}", ("isis",)),
    _Rule(r"{LEAD}(?:area|domain)-password{SP}{V}", ("area-password", "domain-password")),
    _Rule(r"{LEAD}ppp{SP}(?:chap|pap)(?:{SP}\S{1,64}){0,3}?{SP}password{SP}(?:[0-9]{SP}{V}|{V})", ("ppp",)),
    _Rule(r"{B}wpa-psk{SP}(?:ascii|hex){SP}(?:[0-9]{SP})?{V}", ("wpa-psk",)),
    _Rule(r"{LEAD}failover{SP}key{SP}(?:hex{SP})?{V}", ("failover",)),
    # ---- Juniper Junos: `$9$` is its reversible encryption, a secret whatever the keyword.
    _Rule(r"\"(?P<v1>\$9\$[^\"\n]{1,512})\"", ("$9$",)),
    _Rule(r"\"(?P<v1>[^\"\n]{1,512})\";[ \t]*## SECRET-DATA", ("secret-data",)),
    _Rule(r"{B}(?:encrypted|simple)-password{SP}{J}(?:{END}|(?=[ \t]*;))", ("encrypted-password", "simple-password")),
    _Rule(r"{B}pre-shared-key{SP}(?:ascii-text|hexadecimal){SP}{J}(?:{END}|(?=[ \t]*;))", ("pre-shared-key",)),
    _Rule(r"{B}(?:secret|key|password|community|passphrase){SP}\"(?P<v1>[^\"\n]{1,512})\"(?=;)", ('";',)),
    _Rule(r"{LEAD}set{SP}\S{1,64}[^\n]{0,400}?[ \t](?:secret|key|password){SP}{J}{END}", ("set",)),
    _Rule(r"{LEAD}set{SP}snmp{SP}community{SP}{J}(?:{END}|(?={SP}(?:authorization|clients|view)\b))", ("community",)),
    _Rule(r"{LEAD}community{SP}{J}(?=[ \t]*\{)", ("community",)),
    # ---- FortiOS: `set <field> [ENC] <value>` inside `config ... end` blocks.
    _Rule(
        r"{LEAD}set{SP}(?:password|passwd|psksecret|secret|key|passphrase|sae-password|auth-pwd|priv-pwd"
        r"|authentication-key|ppk-secret|ldap-password|sso-password|key-string|server-key|tacacs-key|shared-secret)"
        r"(?:{SP}ENC)?{SP}{V}{END}",
        ("set",),
    ),
    # ---- Palo Alto PAN-OS: XML elements, and `phash` in the set format. Not a plist's `<key>`.
    _Rule(
        r"<(?P<tag>key|password|phash|secret|passphrase|authpwd|privpwd|auth-password|priv-password|community-string"
        r"|bind-password|shared-secret|api-key)>(?P<v1>[^<\n]{1,4096}+)</(?P=tag)>"
        r"(?![ \t\r\n]{0,64}<(?:string|integer|real|true|false|date|data|dict|array)\b)",
        ("</",),
    ),
    _Rule(r"{B}phash{SP}{V}", ("phash",)),
    # ---- Generic `NAME=VALUE` / `NAME: VALUE` (env files, YAML, JSON, INI, --flags). Last, so
    # a value an earlier rule masked is already `<redacted>` here.
    _Rule(
        r"(?:pass(?:word|wd|phrase)|pwd|secret|token|credential|psk|key)(?P<n>[A-Za-z0-9_.\-]{0,60}+)[\"']?[ \t]*[:=]"
        r"[ \t]*{P}(?=[ \t]*(?:[,;})#]|\r?$))",
        ("pass", "pwd", "secret", "token", "credential", "psk", "key"),
        fold=True,
        accept=_secret_assignment,
    ),
)

# A private key block, BEGIN to END, becomes one placeholder. A block without its END line
# (cut off when pasted) loses its base64 and header lines. An END further away than any real
# key is not this block's.
_PEM_BEGIN = re.compile(r"-----BEGIN [A-Z0-9 ]{0,40}PRIVATE KEY(?: BLOCK)?-----")
_PEM_END = re.compile(r"-----END [A-Z0-9 ]{0,40}PRIVATE KEY(?: BLOCK)?-----")
_PEM_BODY = re.compile(r"(?:(?:\r?\n|\\n)(?:[A-Za-z0-9+/=]{1,4096}+|[A-Za-z][A-Za-z\-]{0,39}: [^\r\n]{0,200}+))*+")
_PEM_MAX_CHARS = 64_000


def _mask_private_keys(text: str) -> tuple[str, int]:
    if "PRIVATE KEY" not in text:
        return text, 0
    pieces: list[str] = []
    count = position = 0
    end: re.Match[str] | None = None
    ends_left = True
    while (begin := _PEM_BEGIN.search(text, position)) is not None:
        if ends_left and (end is None or end.start() < begin.end()):
            # Each search starts past the previous END, so the text is scanned once.
            end = _PEM_END.search(text, begin.end())
            ends_left = end is not None
        if end is not None and begin.end() <= end.start() <= begin.end() + _PEM_MAX_CHARS:
            stop = end.end()
        else:
            stop = _PEM_BODY.match(text, begin.end()).end()
        pieces += [text[position : begin.start()], PRIVATE_KEY_PLACEHOLDER]
        count += 1
        position = stop
    pieces.append(text[position:])
    return "".join(pieces), count


def mask_secrets(text: str) -> tuple[str, int]:
    """`text` with each obvious secret value replaced by `<redacted>`, and how many were masked."""
    if not text:
        return text, 0
    text, total = _mask_private_keys(text)
    lower = text.lower()
    for rule in _RULES:
        if any(hint in lower for hint in rule.hints):
            text, count = rule.apply(text)
            total += count
    return text, total


# ----------------------------------------------------------------------------- wrappers
def _inner_attribute(wrapper: object, name: str) -> Any:
    inner = wrapper.__dict__.get("inner")
    if inner is None or name.startswith("__"):
        raise AttributeError(name)
    return getattr(inner, name)


class RedactingProvider:
    """An `LLMProvider` that masks every message before `inner` sees it.

    Wrap the outermost provider (a `FallbackProvider` too), so every provider it tries
    reads the masked text. Only `stream` belongs to the wrapper: every other attribute,
    read or written, is the inner provider's (`name`, and a FakeProvider's `reply` and
    `calls` in tests). The reply, its usage and its errors come back unchanged, and the
    caller's messages are never modified.
    """

    def __init__(self, inner: LLMProvider) -> None:
        object.__setattr__(self, "inner", inner)

    async def stream(self, messages: list[ChatMessage], options: GenerationOptions) -> LLMStream:
        masked: list[ChatMessage] = []
        total = 0
        for message in messages:
            content, count = mask_secrets(message.content)
            masked.append(dataclasses.replace(message, content=content) if count else message)
            total += count
        if total:
            log.info("secrets masked before the model", extra={"redacted": total})
        return await self.inner.stream(masked, options)

    def __getattr__(self, name: str) -> Any:
        return _inner_attribute(self, name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self.inner, name, value)


class RedactingEmbeddings(Embeddings):
    """Embeddings that mask every text before `inner` embeds it.

    Every attribute except the four embed methods comes from the inner instance
    (`dimensions`, `configured`, ...), so `embedding_model_id` and `is_configured` give
    the same answer for the wrapper as for the inner instance. Masking a stored chunk and
    a query the same way keeps them comparable.
    """

    def __init__(self, inner: Embeddings) -> None:
        object.__setattr__(self, "inner", inner)

    @property
    def model(self) -> str:
        # What `embedding_model_id` reads: the inner model, or the inner class name if it has no model.
        return getattr(self.inner, "model", type(self.inner).__name__)

    def _mask(self, texts: list[str]) -> list[str]:
        masked, total = [], 0
        for text in texts:
            content, count = mask_secrets(text)
            masked.append(content)
            total += count
        if total:
            log.info("secrets masked before embedding", extra={"redacted": total})
        return masked

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self.inner.embed_documents(self._mask(texts))

    def embed_query(self, text: str) -> list[float]:
        return self.inner.embed_query(self._mask([text])[0])

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return await self.inner.aembed_documents(self._mask(texts))

    async def aembed_query(self, text: str) -> list[float]:
        return await self.inner.aembed_query(self._mask([text])[0])

    def __getattr__(self, name: str) -> Any:
        return _inner_attribute(self, name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self.inner, name, value)
