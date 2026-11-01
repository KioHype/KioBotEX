"""Constantes, limites e funções de validação usadas pelo app e pelo relé."""
import hashlib
import re
import secrets
import socket

APP_NAME = "KioBot EX"
VERSION = "0.2.0"

# Endereço do relé para salas pela internet (ex.: "wss://meu-rele.exemplo.com").
# Fica vazio até você hospedar um relé. Também dá para mudar em Configurações.
DEFAULT_RELAY_URL = ""
LAN_PORT = 8765

# Código da sala: 8 caracteres, sem letras/números que se confundem (I, L, O, 0, 1).
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 8
_CODE_RE = re.compile(rf"^[{CODE_ALPHABET}]{{{CODE_LENGTH}}}$")

# Limites (segurança e estabilidade)
AUDIO_EXTS = (".mp3", ".wav", ".ogg")
IMAGE_EXTS = (".png", ".jpg")
MAX_SOUND_BYTES = 5 * 1024 * 1024
MAX_IMAGE_BYTES = 1 * 1024 * 1024
MAX_SOUNDS = 100
CHUNK_BYTES = 192 * 1024
MAX_GUESTS = 20
MAX_TIMERS = 5
MAX_NAME_LEN = 40
MAX_NICK_LEN = 20
HOST_MAX_MSG = 512 * 1024
GUEST_MAX_MSG = 4 * 1024


def new_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


def normalize_code(text) -> str:
    """Aceita 'abcd-2345', 'ABCD 2345' etc. Retorna '' se inválido."""
    t = re.sub(r"[\s\-_]", "", str(text or "")).upper()
    return t if _CODE_RE.match(t) else ""


def format_code(code: str) -> str:
    return f"{code[:4]}-{code[4:]}" if len(code) == CODE_LENGTH else code


_IPV4_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}(:\d{1,5})?$")


def _default_scheme(host: str) -> str:
    """IP ou localhost = rede local (ws://). Nome de domínio = internet com criptografia (wss://)."""
    h = host.lower()
    return "ws://" if (_IPV4_RE.match(h) or h.startswith("localhost")) else "wss://"


def parse_invite(text):
    """Interpreta o convite colado pelo usuário.

    Formatos aceitos:
      ABCD-2345                      -> (None, 'ABCD2345')   usa o relé padrão
      192.168.0.10:8765/ABCD2345     -> ('ws://192.168.0.10:8765', 'ABCD2345')
    Retorna (url_ou_None, codigo). Código vazio = convite inválido.
    """
    t = (text or "").strip()
    if "/" in t:
        head, _, tail = t.rpartition("/")
        code = normalize_code(tail)
        if not code:
            return None, ""
        head = head.strip()
        if head:
            if not head.lower().startswith(("ws://", "wss://")):
                head = _default_scheme(head) + head
            if not re.fullmatch(r"wss?://[A-Za-z0-9.\-]+(:\d{1,5})?", head):
                return None, ""
            return head, code
        return None, code
    return None, normalize_code(t)


def normalize_relay_url(text) -> str:
    """Aceita o endereço do jeito que o serviço mostra (https://...) e converte para ws/wss."""
    t = (text or "").strip().rstrip("/")
    if not t:
        return ""
    low = t.lower()
    if low.startswith("https://"):
        return "wss://" + t[8:]
    if low.startswith("http://"):
        return "ws://" + t[7:]
    if low.startswith(("ws://", "wss://")):
        return t
    return _default_scheme(t) + t


def is_temporary_tunnel(url) -> bool:
    """Endereços .trycloudflare.com são temporários: morrem quando o túnel fecha."""
    return "trycloudflare.com" in str(url or "").lower()


def clean_text(value, maxlen: int) -> str:
    """Remove caracteres de controle/quebras de linha e limita o tamanho."""
    s = "".join(ch for ch in str(value or "") if ch.isprintable())
    return s.strip()[:maxlen]


def clean_nick(value) -> str:
    return clean_text(value, MAX_NICK_LEN) or "Convidado"


def valid_color(c) -> bool:
    return isinstance(c, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", c) is not None


def valid_id(x) -> bool:
    return isinstance(x, str) and re.fullmatch(r"[0-9a-f]{32}", x) is not None


def content_id(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:32]


def detect_audio(data: bytes):
    """Identifica o tipo pelo conteúdo (não pelo nome). Retorna extensão ou None."""
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return ".wav"
    if data[:4] == b"OggS":
        return ".ogg"
    if data[:3] == b"ID3":
        return ".mp3"
    if len(data) > 2 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0:
        return ".mp3"
    return None


def detect_image(data: bytes):
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    return None


def local_ip() -> str:
    """IP da máquina na rede local (sem enviar nada pela rede)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()
