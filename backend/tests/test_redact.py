"""Secrets are masked before any model or embeddings provider reads a text (app.llm.redact).

Every secret here is synthetic: each value contains FAKE (any case) or is the fake type-7
string 0822455D0A16, so a surviving value is easy to spot.
"""

import logging
import time

import pytest

from app.llm.base import ChatMessage, GenerationOptions, LLMStream, LLMTimeout, LLMUnavailable, ProviderInfo
from app.llm.fake import FakeProvider
from app.llm.redact import PLACEHOLDER, RedactingEmbeddings, RedactingProvider, mask_secrets
from app.rag.embeddings import KeywordHashEmbeddings, OpenAICompatibleEmbeddings, embedding_model_id, is_configured

R = PLACEHOLDER
TYPE7 = "0822455D0A16"


def survivors(text: str) -> bool:
    return "fake" in text.lower() or TYPE7 in text


# (id, input, expected): the value goes, the keyword and type stay.
MASKED = [
    # Cisco IOS / IOS-XE / NX-OS / ASA
    ("ios-enable-secret-9", "enable secret 9 $9$FAKEFAKEFAKEFAKE", f"enable secret 9 {R}"),
    ("ios-enable-secret-5", "enable secret 5 $1$FAKE$FAKEFAKEFAKEFAKE", f"enable secret 5 {R}"),
    ("ios-enable-password-7", f"enable password 7 {TYPE7}", f"enable password 7 {R}"),
    ("ios-enable-password-plain", "enable password FakeEnable1", f"enable password {R}"),
    (
        "ios-enable-scrypt",
        "enable algorithm-type scrypt secret FakeScrypt1",
        f"enable algorithm-type scrypt secret {R}",
    ),
    ("asa-enable-encrypted", "enable password FakeAsaEnable encrypted", f"enable password {R} encrypted"),
    (
        "ios-username-secret",
        "username admin privilege 15 secret 5 $1$FAKE$FAKEFAKE",
        f"username admin privilege 15 secret 5 {R}",
    ),
    ("ios-username-password-0", "username bob password 0 FakeBob1", f"username bob password 0 {R}"),
    (
        "nxos-username",
        "username admin password 5 $5$FAKE$FAKEFAKE role network-admin",
        f"username admin password 5 {R} role network-admin",
    ),
    (
        "asa-username",
        "username admin password FakeAsaUser encrypted privilege 15",
        f"username admin password {R} encrypted privilege 15",
    ),
    (
        "ios-line-password-7",
        f"line vty 0 4\n password 7 {TYPE7}\n login local",
        f"line vty 0 4\n password 7 {R}\n login local",
    ),
    ("ios-line-password", "line con 0\n password FakeConsole\n login", f"line con 0\n password {R}\n login"),
    ("cli-prompt", "R1(config-line)#password FakeConsole2", f"R1(config-line)#password {R}"),
    ("inline-code", "I typed `enable secret FakeInline` and it failed", f"I typed `enable secret {R}` and it failed"),
    ("markdown-list", "- enable secret 5 $1$FAKE$FAKE", f"- enable secret 5 {R}"),
    ("crlf", f"line vty 0 4\r\n password 7 {TYPE7}\r\n", f"line vty 0 4\r\n password 7 {R}\r\n"),
    ("asa-passwd", "passwd FakeAsaPasswd encrypted", f"passwd {R} encrypted"),
    ("ios-config-key", "key config-key password-encrypt FakeMasterKey", f"key config-key password-encrypt {R}"),
    ("snmp-community", "snmp-server community FakeCommunity RO 10", f"snmp-server community {R} RO 10"),
    (
        "nxos-snmp-community",
        "snmp-server community FakeNx group network-operator",
        f"snmp-server community {R} group network-operator",
    ),
    ("snmp-host", "snmp-server host 10.1.1.1 version 2c FakeTrapComm", f"snmp-server host 10.1.1.1 version 2c {R}"),
    (
        "asa-snmp-host",
        "snmp-server host inside 10.1.1.1 community FakeAsaComm version 2c",
        f"snmp-server host inside 10.1.1.1 community {R} version 2c",
    ),
    (
        "snmp-v3-user",
        "snmp-server user NMS NMSGROUP v3 auth sha FakeAuthPass priv aes 128 FakePrivPass",
        f"snmp-server user NMS NMSGROUP v3 auth sha {R} priv aes 128 {R}",
    ),
    (
        "nxos-snmp-user",
        "snmp-server user admin network-admin auth md5 0xFAKE0001 priv aes-128 0xFAKE0002 localizedkey",
        f"snmp-server user admin network-admin auth md5 {R} priv aes-128 {R} localizedkey",
    ),
    ("key-chain", f"key chain OSPF\n key 1\n  key-string 7 {TYPE7}", f"key chain OSPF\n key 1\n  key-string 7 {R}"),
    ("key-string", "  key-string FakeKeyString", f"  key-string {R}"),
    ("hsrp", "standby 1 authentication md5 key-string FakeHsrp", f"standby 1 authentication md5 key-string {R}"),
    ("isakmp", "crypto isakmp key FakeIsakmp address 203.0.113.2", f"crypto isakmp key {R} address 203.0.113.2"),
    ("isakmp-6", "crypto isakmp key 6 FakeIsakmp6 address 203.0.113.2", f"crypto isakmp key 6 {R} address 203.0.113.2"),
    (
        "ios-keyring",
        "crypto keyring KR\n pre-shared-key address 203.0.113.2 key FakeKeyring",
        f"crypto keyring KR\n pre-shared-key address 203.0.113.2 key {R}",
    ),
    ("ikev2-local", "  pre-shared-key local FakeLocalPsk", f"  pre-shared-key local {R}"),
    ("ikev2-remote-6", "  pre-shared-key remote 6 FakeRemotePsk", f"  pre-shared-key remote 6 {R}"),
    (
        "asa-ikev1",
        "tunnel-group 203.0.113.2 ipsec-attributes\n ikev1 pre-shared-key FakeAsaPsk",
        f"tunnel-group 203.0.113.2 ipsec-attributes\n ikev1 pre-shared-key {R}",
    ),
    (
        "asa-ikev2-8",
        " ikev2 remote-authentication pre-shared-key 8 FakeAsaPsk2",
        f" ikev2 remote-authentication pre-shared-key 8 {R}",
    ),
    ("tacacs-key", f"tacacs-server key 7 {TYPE7}", f"tacacs-server key 7 {R}"),
    ("tacacs-host", "tacacs-server host 10.1.1.5 key FakeTacacs", f"tacacs-server host 10.1.1.5 key {R}"),
    (
        "radius-host",
        f"radius-server host 10.1.1.6 auth-port 1812 acct-port 1813 key 7 {TYPE7}",
        f"radius-server host 10.1.1.6 auth-port 1812 acct-port 1813 key 7 {R}",
    ),
    (
        "nxos-radius-quoted",
        'radius-server host 10.1.1.6 key 7 "FakeNxRadius" authentication accounting',
        f'radius-server host 10.1.1.6 key 7 "{R}" authentication accounting',
    ),
    (
        "radius-server-block",
        f"radius server ISE\n address ipv4 10.1.1.6\n key 7 {TYPE7}",
        f"radius server ISE\n address ipv4 10.1.1.6\n key 7 {R}",
    ),
    (
        "asa-aaa-key",
        "aaa-server ISE (inside) host 10.1.1.6\n key FakeAaaKey",
        f"aaa-server ISE (inside) host 10.1.1.6\n key {R}",
    ),
    (
        "ospf-key",
        f"interface Gi0/0\n ip ospf authentication-key 7 {TYPE7}",
        f"interface Gi0/0\n ip ospf authentication-key 7 {R}",
    ),
    ("ospf-md5", " ip ospf message-digest-key 1 md5 FakeOspfMd5", f" ip ospf message-digest-key 1 md5 {R}"),
    ("ospf-md5-7", f" ip ospf message-digest-key 1 md5 7 {TYPE7}", f" ip ospf message-digest-key 1 md5 7 {R}"),
    ("ntp", f"ntp authentication-key 1 md5 {TYPE7} 7", f"ntp authentication-key 1 md5 {R} 7"),
    (
        "bgp",
        f"router bgp 65000\n neighbor 10.0.0.2 password 7 {TYPE7}",
        f"router bgp 65000\n neighbor 10.0.0.2 password 7 {R}",
    ),
    ("bgp-plain", " neighbor PEERS password FakeBgp", f" neighbor PEERS password {R}"),
    ("isis", " isis password FakeIsis", f" isis password {R}"),
    ("wpa-psk", " wpa-psk ascii 0 FakeWifiPsk", f" wpa-psk ascii 0 {R}"),
    ("ppp-chap", f"ppp chap password 7 {TYPE7}", f"ppp chap password 7 {R}"),
    ("ppp-pap", "ppp pap sent-username R1 password 0 FakePap", f"ppp pap sent-username R1 password 0 {R}"),
    ("asa-failover", "failover key FakeFailover", f"failover key {R}"),
    # Juniper Junos
    (
        "junos-root",
        'set system root-authentication encrypted-password "$6$FAKE$FAKEFAKE"',
        f'set system root-authentication encrypted-password "{R}"',
    ),
    (
        "junos-secret-data",
        'encrypted-password "$6$FAKE$FAKEFAKE"; ## SECRET-DATA',
        f'encrypted-password "{R}"; ## SECRET-DATA',
    ),
    (
        "junos-radius-9",
        'set system radius-server 10.1.1.6 secret "$9$FAKEFAKE"',
        f'set system radius-server 10.1.1.6 secret "{R}"',
    ),
    (
        "junos-radius-plain",
        "set system radius-server 10.1.1.6 secret FakeJunosRadius",
        f"set system radius-server 10.1.1.6 secret {R}",
    ),
    ("junos-bgp", 'authentication-key "$9$FAKEFAKE";', f'authentication-key "{R}";'),
    (
        "junos-ike",
        'set security ike policy IKE-POL pre-shared-key ascii-text "$9$FAKEFAKE"',
        f'set security ike policy IKE-POL pre-shared-key ascii-text "{R}"',
    ),
    (
        "junos-ike-plain",
        "set security ike policy IKE-POL pre-shared-key ascii-text FakeJunosPsk",
        f"set security ike policy IKE-POL pre-shared-key ascii-text {R}",
    ),
    (
        "junos-ntp",
        'set system ntp authentication-key 1 type md5 value "FakeJunosNtp"',
        f'set system ntp authentication-key 1 type md5 value "{R}"',
    ),
    (
        "junos-any-9",
        'set protocols ospf area 0 interface ge-0/0/0.0 authentication md5 1 key "$9$FAKEFAKE"',
        f'set protocols ospf area 0 interface ge-0/0/0.0 authentication md5 1 key "{R}"',
    ),
    (
        "junos-snmp-set",
        "set snmp community FakeJunosComm authorization read-only",
        f"set snmp community {R} authorization read-only",
    ),
    (
        "junos-snmp-curly",
        "snmp {\n    community FakeJunosComm {\n        authorization read-only;\n    }\n}",
        f"snmp {{\n    community {R} {{\n        authorization read-only;\n    }}\n}}",
    ),
    (
        "junos-curly-plain",
        'radius-server {\n    10.1.1.6 secret "FakeCurly";\n}',
        f'radius-server {{\n    10.1.1.6 secret "{R}";\n}}',
    ),
    # FortiOS
    (
        "forti-admin",
        'config system admin\n    edit "admin"\n        set password ENC SH2FAKEFAKEFAKE=\n    next\nend',
        f'config system admin\n    edit "admin"\n        set password ENC {R}\n    next\nend',
    ),
    ("forti-psk", "        set psksecret ENC FAKEFAKEFAKE==", f"        set psksecret ENC {R}"),
    ("forti-passwd", "        set passwd FakeFortiPass", f"        set passwd {R}"),
    ("forti-radius", "        set secret ENC FAKEFAKE", f"        set secret ENC {R}"),
    ("forti-tacacs", "        set key FakeFortiTac", f"        set key {R}"),
    # Palo Alto PAN-OS
    (
        "panos-psk",
        "<pre-shared-key>\n  <key>-AQ==FAKEFAKEFAKE=</key>\n</pre-shared-key>",
        f"<pre-shared-key>\n  <key>{R}</key>\n</pre-shared-key>",
    ),
    ("panos-phash", "<phash>$1$FAKE$FAKEFAKE</phash>", f"<phash>{R}</phash>"),
    ("panos-password", "<password>FakePanPassword</password>", f"<password>{R}</password>"),
    ("panos-secret", "<secret>-AQ==FAKE</secret>", f"<secret>{R}</secret>"),
    ("panos-set-phash", "set mgt-config users admin phash $5$FAKE$FAKE", f"set mgt-config users admin phash {R}"),
    # Generic
    (
        "pem",
        "-----BEGIN " + "RSA PRIVATE KEY-----\nMIIEFAKEFAKE\nFAKEFAKEFAKE==\n-----END " + "RSA PRIVATE KEY-----",
        "<redacted private key>",
    ),
    (
        "pem-in-prose",
        "My key:\n-----BEGIN " + "OPENSSH PRIVATE KEY-----\nb3BlbnNzaFAKE\n-----END " + "OPENSSH PRIVATE KEY-----\n"
        "why does ssh fail?",
        "My key:\n<redacted private key>\nwhy does ssh fail?",
    ),
    ("pem-cut-off", "-----BEGIN " + "PRIVATE KEY-----\nMIIEvQFAKEFAKE\nFAKEFAKE", "<redacted private key>"),
    (
        "pem-json",
        '{"private_key": "-----BEGIN PRIVATE KEY-----\\nMIIEFAKE\\n-----END PRIVATE KEY-----\\n"}',
        '{"private_key": "<redacted private key>\\n"}',
    ),
    ("aws-key-id", "aws_access_key_id = AKIAFAKEFAKEFAKEFAKE", f"aws_access_key_id = {R}"),
    ("aws-secret", "aws_secret_access_key = FAKEfakeFAKEfakeFAKEfakeFAKEfakeFAKEfake", f"aws_secret_access_key = {R}"),
    (
        "bearer-header",
        'curl -H "Authorization: ' + 'Bearer FAKEtoken0123456789" https://api.example.com',
        f'curl -H "Authorization: Bearer {R}" https://api.example.com',
    ),
    ("basic-header", "Authorization: Basic FAKEdXNlcjpwYXNz", f"Authorization: Basic {R}"),
    ("bearer", "send Bearer FAKE0123456789abcdef with it", f"send Bearer {R} with it"),
    ("jwt", "token eyJhbGciOiJIUzI1NiJ9" + ".eyJzdWIiOiJGQUtFIn0.FAKEsignatureFAKE here", f"token {R} here"),
    ("env", "DB_PASSWORD=FakeDbPass\nDB_HOST=db.internal", f"DB_PASSWORD={R}\nDB_HOST=db.internal"),
    (
        "yaml",
        "database:\n  user: app\n  password: FakeYamlPass\n  port: 5432",
        f"database:\n  user: app\n  password: {R}\n  port: 5432",
    ),
    (
        "json",
        '{"username": "app", "password": "FakeJsonPass", "port": 5432}',
        f'{{"username": "app", "password": "{R}", "port": 5432}}',
    ),
    ("export", 'export API_KEY="FakeApiKey"', f'export API_KEY="{R}"'),
    ("github", "GITHUB_TOKEN=ghp_FAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKE", f"GITHUB_TOKEN={R}"),
    ("github-pat", "github_pat_FAKEFAKEFAKEFAKEFAKEFA_FAKE", R),
    ("gitlab", "glpat-FAKEFAKEFAKEFAKEFAKE", R),
    ("slack", "xoxb-FAKE-FAKE-FAKEFAKEFAKE", R),
    ("openai", "OPENAI_API_KEY=sk-proj-FAKEFAKEFAKE0123456789", f"OPENAI_API_KEY={R}"),
    ("google-oauth", "client_secret: GOCSPX-FAKEFAKEFAKEFAKEFAKE", f"client_secret: {R}"),
    (
        "connection-string",
        "DATABASE_URL=postgresql://app:FakeConnPass@db.internal:5432/app",
        f"DATABASE_URL=postgresql://app:{R}@db.internal:5432/app",
    ),
    ("cli-flag", "mysql -u root --password=FakeCliPass", f"mysql -u root --password={R}"),
    ("properties", "spring.datasource.password=FakeSpring", f"spring.datasource.password={R}"),
]


@pytest.mark.parametrize(("text", "expected"), [pytest.param(t, e, id=i) for i, t, e in MASKED])
def test_each_secret_value_is_masked_and_its_keyword_kept(text, expected):
    masked, count = mask_secrets(text)
    assert masked == expected
    assert not survivors(masked)
    assert count == expected.count("<redacted")


NOT_MASKED = [
    ("password-encryption", "service password-encryption"),
    ("no-password", "line con 0\n no password\n login"),
    ("aaa", "aaa new-model\naaa authentication login default local\naaa authorization exec default local"),
    ("community-without-value", "snmp-server community"),
    ("asa-password-policy", "password-policy minimum-length 8\nsecurity passwords min-length 10"),
    ("prose", "I forgot my password, how do I reset it on a Cisco 2960?"),
    ("prose-line-start", "password reset not working after reload"),
    ("prose-commands", "What is the difference between enable password and enable secret?"),
    ("prose-community", "the snmp-server community string is wrong"),
    ("prose-bearer", "Bearer authentication is described in RFC 6750."),
    (
        "ios-config",
        "hostname R1\n!\ninterface GigabitEthernet0/1\n description Uplink to core\n"
        " ip address 10.0.0.1 255.255.255.252\n ipv6 address 2001:db8::1/64\n ip ospf authentication message-digest\n"
        " no shutdown\n!\nvlan 20\n name USERS\n!\nip access-list extended MGMT\n"
        " permit tcp 10.10.0.0 0.0.255.255 any eq 22\n deny ip any any log\n!\nroute-map PBR permit 10\n"
        " match ip address MGMT\n set ip next-hop 10.0.0.2\n!\nrouter ospf 1\n network 10.0.0.0 0.0.0.3 area 0\n"
        "!\nkey chain OSPF\n key 1\n!\ncrypto key generate rsa modulus 2048\nusername admin privilege 15\n",
    ),
    (
        "show-output",
        "R1#show running-config | include password|secret\nR1#show ip interface brief\n"
        "Interface              IP-Address      OK? Method Status                Protocol\n"
        "GigabitEthernet0/0     10.0.0.1        YES manual up                    up\n"
        "R1#show ip ospf neighbor\nNeighbor ID     Pri   State           Dead Time   Address         Interface\n"
        "2.2.2.2           1   FULL/DR         00:00:33    10.0.0.2        Gi0/0\n",
    ),
    ("asa-already-masked", " ikev1 pre-shared-key *****"),
    (
        "junos",
        "set interfaces ge-0/0/0 unit 0 family inet address 10.0.0.1/30\n"
        "set protocols ospf area 0.0.0.0 interface ge-0/0/0.0\n"
        "set policy-options community CUSTOMERS members 65000:100",
    ),
    (
        "fortios",
        'config firewall policy\n    edit 1\n        set srcintf "port1"\n        set action accept\n    next\nend',
    ),
    ("panos", '<entry name="allow-web"><from><member>trust</member></from><action>allow</action></entry>'),
    ("plist", "<key>SSID_STR</key>\n<string>Lab</string>"),
    ("certificate", "-----BEGIN CERTIFICATE-----\nMIIBpublic\n-----END CERTIFICATE-----"),
    (
        "markdown",
        "## Securing the console\n\n1. Use **`enable secret`** instead of `enable password`.\n"
        "2. Turn on `service password-encryption`.\n3. Never share your password: rotate it every 90 days.\n",
    ),
    (
        "python",
        "def login(username: str, password: str) -> bool:\n"
        '    secret = get_secret("db")\n'
        '    token = os.environ["API_TOKEN"]\n'
        "    self.password = password\n"
        "    return check(password=password)\n",
    ),
    ("settings", "max_tokens: 4096\npassword_min_length: 12\ntoken_url: https://auth.example.com/token"),
    ("references", "token: ${TOKEN}\napi_key: settings.api_key\nAuthorization: Bearer $TOKEN"),
    ("prose-colon", "Password: I forgot it"),
    ("cve", "CVE-2026-12345 affects password parsing"),
    ("sha256-hash", "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"),
    (
        "public-key",
        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakePublicMaterialOnly engineer@example",
    ),
]


@pytest.mark.parametrize("text", [pytest.param(t, id=i) for i, t in NOT_MASKED])
def test_what_is_not_a_secret_stays_byte_identical(text):
    assert mask_secrets(text) == (text, 0)


@pytest.mark.parametrize("text", [pytest.param(t, id=i) for i, t, _ in MASKED])
def test_masking_twice_changes_nothing_more(text):
    once, _ = mask_secrets(text)
    assert mask_secrets(once) == (once, 0)


def test_a_two_megabyte_config_is_masked_quickly():
    block = (
        "hostname R1\n!\nenable secret 9 $9$FAKEFAKEFAKEFAKE\n"
        "username admin privilege 15 secret 9 $9$FAKEFAKEFAKEFAKE\n!\n"
        "interface GigabitEthernet0/1\n description Uplink to core\n ip address 10.0.0.1 255.255.255.252\n"
        f" ip ospf message-digest-key 1 md5 7 {TYPE7}\n no shutdown\n!\n"
        "ip access-list extended MGMT\n permit tcp 10.10.0.0 0.0.255.255 any eq 22\n deny ip any any log\n!\n"
        f"snmp-server community FakeCommunity RO 10\ntacacs-server host 10.1.1.5 key 7 {TYPE7}\n"
        f"line vty 0 4\n password 7 {TYPE7}\n transport input ssh\n!\n"
    )
    copies = 2_000_000 // len(block) + 1
    started = time.perf_counter()
    masked, count = mask_secrets(block * copies)
    assert time.perf_counter() - started < 2
    assert count == 6 * copies
    assert not survivors(masked)


def test_empty_text():
    assert mask_secrets("") == ("", 0)


# ----------------------------------------------------------------------------- the provider
SECRET_LINE = "enable secret 5 $1$FAKE$FAKEFAKE"


async def test_the_model_reads_masked_messages_and_the_reply_comes_back_unchanged(caplog):
    inner = FakeProvider(reply=lambda messages: "Use enable secret, not enable password.")
    provider = RedactingProvider(inner)
    messages = [ChatMessage("system", "You are a tutor."), ChatMessage("user", f"{SECRET_LINE}\nIs this right?")]
    caplog.set_level(logging.INFO, logger="app.llm")

    stream = await provider.stream(messages, GenerationOptions(model="m"))
    parts = [part async for part in stream.parts]

    sent = inner.calls[-1]
    assert sent[1] == ChatMessage("user", f"enable secret 5 {R}\nIs this right?")
    assert sent[0] is messages[0]  # nothing to mask: passed as it is
    assert messages[1].content == f"{SECRET_LINE}\nIs this right?"  # the caller's message is untouched
    assert stream.info == ProviderInfo("fake", "m")
    assert "".join(part.text for part in parts) == "Use enable secret, not enable password."
    assert parts[-1].finish_reason == "stop"
    assert parts[-1].usage.input_tokens == sum(len(m.content) for m in sent) // 4
    records = [r for r in caplog.records if r.name == "app.llm" and hasattr(r, "redacted")]
    assert [r.redacted for r in records] == [1]
    assert not any(survivors(r.getMessage()) for r in caplog.records)


async def test_the_inner_stream_and_errors_pass_through_as_they_are():
    class Stub:
        name = "stub"

        def __init__(self):
            self.result = LLMStream(info=ProviderInfo("stub", "m"), parts=None, extra={"x": 1})

        async def stream(self, messages, options):
            return self.result

    stub = Stub()
    assert await RedactingProvider(stub).stream([ChatMessage("user", "hi")], GenerationOptions()) is stub.result

    error = LLMUnavailable("down")
    with pytest.raises(LLMUnavailable) as raised:
        await RedactingProvider(FakeProvider(fail_before_first=error)).stream(
            [ChatMessage("user", "hi")], GenerationOptions()
        )
    assert raised.value is error

    late = LLMTimeout("stalled")
    stream = await RedactingProvider(FakeProvider(fail_after=(1, late))).stream(
        [ChatMessage("user", "one two three")], GenerationOptions()
    )
    with pytest.raises(LLMTimeout) as raised_late:
        _ = [part async for part in stream.parts]
    assert raised_late.value is late


def test_the_provider_reads_and_writes_the_inner_attributes():
    inner = FakeProvider(name="primary")
    provider = RedactingProvider(inner)
    assert provider.name == "primary"
    assert provider.calls is inner.calls
    provider.token_delay_s = 0.5  # what a test fixture would set on the app's provider
    assert inner.token_delay_s == 0.5
    with pytest.raises(AttributeError):
        _ = provider.missing


# --------------------------------------------------------------------------- the embeddings
class RecordingEmbeddings(KeywordHashEmbeddings):
    def __init__(self):
        super().__init__(1536, model="fake:recording")
        self.seen: list[str] = []

    def embed_documents(self, texts):
        self.seen += texts
        return super().embed_documents(texts)

    def embed_query(self, text):
        self.seen.append(text)
        return super().embed_query(text)


async def test_the_embeddings_provider_reads_masked_text():
    inner = RecordingEmbeddings()
    wrapper = RedactingEmbeddings(inner)
    secret, masked = "snmp-server community FakeCommunity RO", f"snmp-server community {R} RO"

    assert wrapper.embed_query(secret) == KeywordHashEmbeddings(1536).embed_query(masked)
    assert wrapper.embed_documents([secret, "ospf area 0"]) == KeywordHashEmbeddings(1536).embed_documents(
        [masked, "ospf area 0"]
    )
    await wrapper.aembed_query(secret)
    await wrapper.aembed_documents([secret])
    assert inner.seen == [masked, masked, "ospf area 0", masked, masked]


def test_the_embeddings_helpers_answer_as_for_the_inner_instance():
    for inner in (
        KeywordHashEmbeddings(1536, model="fake:text-embedding-3-small"),
        OpenAICompatibleEmbeddings(base_url="http://embeddings.test/v1", api_key="", model="m", dimensions=1536),
    ):
        wrapper = RedactingEmbeddings(inner)
        assert embedding_model_id(wrapper) == embedding_model_id(inner)
        assert is_configured(wrapper) is is_configured(inner)
        assert wrapper.dimensions == inner.dimensions

    class Unnamed(KeywordHashEmbeddings):
        def __getattribute__(self, name):
            if name == "model":
                raise AttributeError(name)
            return super().__getattribute__(name)

    unnamed = Unnamed(1536)
    assert embedding_model_id(RedactingEmbeddings(unnamed)) == embedding_model_id(unnamed) == "Unnamed"
