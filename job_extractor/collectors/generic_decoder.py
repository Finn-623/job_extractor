"""Generic detail-response decoder contract (STEP 54B).

The runtime detail contract carries a ``decoder`` dict describing how the
observed detail response must be transformed into a JSON object. Supported
generic modes:

``PLAIN_JSON``
    The response body already is a JSON object.

``AES_CBC_ENVELOPE``
    The body is ``{"<payload_field>": <base64 ciphertext>, "<envelope_field>":
    <key-string>}`` decrypted with AES-CBC and PKCS#7 unpadding, with the IV
    sourced from runtime-state evidence (``iv_source="runtime_state.<key>"``)
    or the observed value carried in the spec.

Decoders are pure functions over (payload, decoder-spec, runtime_state).
"""
from __future__ import annotations

import base64
import binascii
import json
from typing import Any, Callable

DecoderFn = Callable[[Any, dict[str, Any], dict[str, Any]], dict[str, Any]]


def _runtime_state_value(state: dict[str, Any], source: str) -> Any:
    """Resolve an IV: either the observed value in the spec or a
    ``runtime_state.<key>`` pointer (case-insensitive key lookup)."""
    spec_iv = source if not str(source or "").lower().startswith("runtime_state.") else None
    if spec_iv:
        return spec_iv
    if not isinstance(state, dict):
        return None
    key = str(source or "").split(".", 1)[1]
    lowered = {str(k).lower(): v for k, v in state.items()}
    return lowered.get(key.lower())


def _plain_json(payload: Any, spec: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, (str, bytes)):
        value = json.loads(payload)
        return value if isinstance(value, dict) else {}
    return {}


def _aes_cbc_envelope(payload: Any, spec: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    envelope = payload.get(spec.get("payload_field") or "data")
    key = payload.get(spec.get("envelope_field") or "key")
    # IV provenance: observed value carried in the contract first, then the
    # runtime-state pointer (both captured at discovery time).
    iv = spec.get("iv")
    if not isinstance(iv, str) or not iv:
        iv = _runtime_state_value(state, str(spec.get("iv_source") or ""))
    if not isinstance(envelope, str) or not isinstance(key, str) or not isinstance(iv, str):
        return {}
    from Crypto.Cipher import AES  # imported lazily: only decoders need it
    from Crypto.Util.Padding import unpad

    try:
        cipher = AES.new(key.encode(), AES.MODE_CBC, iv.encode())
        decrypted = unpad(cipher.decrypt(base64.b64decode(envelope)), 16).decode()
    except (ValueError, KeyError, TypeError, binascii.Error, UnicodeDecodeError):
        return {}
    try:
        value = json.loads(decrypted)
    except (ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


_DECODERS: dict[str, DecoderFn] = {
    "PLAIN_JSON": _plain_json,
    "AES_CBC_ENVELOPE": _aes_cbc_envelope,
}


def _resolve_mode(spec: dict[str, Any] | None) -> str:
    """Resolve the decoder mode from the contract.

    An explicit mode wins; otherwise the shape decides: a spec carrying an
    ``envelope_field`` is an envelope transform, anything else is plain JSON.
    Discovery emits evidence-only specs (no transform vocabulary), so this
    inference is the single naming authority.
    """
    mode = str((spec or {}).get("mode") or "").upper()
    if mode:
        return mode
    if (spec or {}).get("envelope_field"):
        return "AES_CBC_ENVELOPE"
    return "PLAIN_JSON"


def decode_detail_payload(payload: Any, spec: dict[str, Any] | None, state: dict[str, Any] | None = None) -> dict[str, Any]:
    """Decode a detail response through the plan's generic decoder contract.

    Unknown/missing spec falls back to plain-JSON-object passthrough; a
    decoder failure returns ``{}`` so callers classify EMPTY_DETAIL instead
    of crashing.
    """
    mode = _resolve_mode(spec)
    decoder = _DECODERS.get(mode)
    if decoder is None:
        return {}
    return decoder(payload, spec or {}, state or {})


def resolve_decoder_runtime_state(spec: dict[str, Any] | None, state: dict[str, Any] | None) -> dict[str, Any]:
    """Return the runtime-state subset a decoder spec needs (for evidence)."""
    source = str((spec or {}).get("iv_source") or "")
    if source.lower().startswith("runtime_state."):
        key = source.split(".", 1)[1].lower()
        return {str(k): v for k, v in (state or {}).items() if str(k).lower() == key}
    return {}
