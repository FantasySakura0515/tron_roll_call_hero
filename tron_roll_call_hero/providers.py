from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping


DEFAULT_PROVIDER = "thu"


@dataclass(frozen=True)
class ProviderCapabilities:
    number: bool = False
    radar: bool = False
    qrcode: bool = False
    course_discovery: bool = False
    teacher_rollcall: bool = False
    manual_qr: bool = False
    local_scanner: bool = False
    bot_adapter: bool = False
    webview_cookie_sync: bool = False
    direct_code_lookup: bool = False

    def to_dict(self) -> Dict[str, bool]:
        return {
            "number": self.number,
            "radar": self.radar,
            "qrcode": self.qrcode,
            "course_discovery": self.course_discovery,
            "teacher_rollcall": self.teacher_rollcall,
            "manual_qr": self.manual_qr,
            "local_scanner": self.local_scanner,
            "bot_adapter": self.bot_adapter,
            "webview_cookie_sync": self.webview_cookie_sync,
            "direct_code_lookup": self.direct_code_lookup,
        }


@dataclass(frozen=True)
class ProviderDefinition:
    key: str
    label: str
    base_url: str
    login_url: str
    auth_flow: str
    rollcalls_url: str = ""
    current_semester_url: str = ""
    courses_url: str = ""
    status: str = "stub"
    support_level: str = ""
    capabilities: ProviderCapabilities = field(default_factory=ProviderCapabilities)
    notes: str = ""
    user_visible: bool = True

    def __post_init__(self) -> None:
        endpoints = tronclass_api_endpoints(self.base_url)
        if not self.rollcalls_url:
            object.__setattr__(self, "rollcalls_url", endpoints["rollcalls_url"])
        if not self.current_semester_url:
            object.__setattr__(self, "current_semester_url", endpoints["current_semester_url"])
        if not self.courses_url:
            object.__setattr__(self, "courses_url", endpoints["courses_url"])

    @property
    def ready(self) -> bool:
        return self.support_level == "ready" or (not self.support_level and self.status == "ready")

    @property
    def daily_ready(self) -> bool:
        return self.ready

    @property
    def effective_support_level(self) -> str:
        if self.support_level:
            return self.support_level
        if self.status == "ready":
            return "ready"
        if self.status in {"experimental", "unsupported"}:
            return self.status
        return "unsupported"

    def to_config(self) -> Dict[str, Any]:
        endpoints = tronclass_api_endpoints(self.base_url)
        return {
            "key": self.key,
            "label": self.label,
            "base_url": self.base_url,
            "login_url": self.login_url,
            "rollcalls_url": self.rollcalls_url or endpoints["rollcalls_url"],
            "current_semester_url": self.current_semester_url or endpoints["current_semester_url"],
            "courses_url": self.courses_url or endpoints["courses_url"],
            "auth_flow": self.auth_flow,
            "status": self.status,
            "support_level": self.effective_support_level,
            "ready": self.ready,
            "daily_ready": self.daily_ready,
            "user_visible": self.user_visible,
            "capabilities": self.capabilities.to_dict(),
            "notes": self.notes,
        }


def tronclass_api_endpoints(base_url: Any) -> Dict[str, str]:
    base = str(base_url or "").strip().rstrip("/")
    return {
        "rollcalls_url": "{}/api/radar/rollcalls?api_version=1.1.0".format(base),
        "current_semester_url": "{}/api/current-semester-info".format(base),
        "courses_url": "{}/api/my-courses?page=1&page_size=50".format(base),
    }


PROVIDERS: Dict[str, ProviderDefinition] = {
    "thu": ProviderDefinition(
        key="thu",
        label="Tunghai University iLearn",
        base_url="https://ilearn.thu.edu.tw",
        login_url=(
            "https://tcidentity.thu.edu.tw/auth/realms/thu/protocol/cas/login"
            "?ui_locales=zh-TW&service=https%3A//ilearn.thu.edu.tw/login&locale=zh_TW"
        ),
        auth_flow="thu_cas",
        status="ready",
        support_level="ready",
        capabilities=ProviderCapabilities(
            number=True,
            radar=True,
            qrcode=True,
            course_discovery=True,
            teacher_rollcall=True,
            manual_qr=True,
            local_scanner=True,
            direct_code_lookup=True,
        ),
        notes="Primary supported provider. Kept compatible with the legacy config.yaml flow.",
    ),
    "fju": ProviderDefinition(
        key="fju",
        label="Fu Jen Catholic University TronClass",
        base_url="https://elearn2.fju.edu.tw",
        login_url="https://elearn2.fju.edu.tw/login",
        auth_flow="tronclass_form_captcha",
        status="ready",
        support_level="ready",
        user_visible=True,
        capabilities=ProviderCapabilities(
            number=True,
            radar=True,
            qrcode=True,
            course_discovery=True,
            teacher_rollcall=True,
            manual_qr=True,
            local_scanner=True,
            direct_code_lookup=True,
        ),
        notes="帳密登入 + 圖形驗證碼自動辨識（ddddocr，選用），失敗 fallback 人工輸入；authenticated TronClass API flows share the common runtime.",
    ),
    "tku": ProviderDefinition(
        key="tku",
        label="Tamkang University TronClass",
        base_url="https://iclass.tku.edu.tw",
        login_url="https://iclass.tku.edu.tw/login?next=/iportal&locale=zh_TW",
        auth_flow="tku_sso_browser",
        status="ready",
        support_level="ready",
        capabilities=ProviderCapabilities(
            number=True,
            radar=True,
            qrcode=True,
            course_discovery=True,
            teacher_rollcall=True,
            manual_qr=True,
            local_scanner=True,
            direct_code_lookup=True,
        ),
        notes="Ready for user-level daily flow. TKU SSO uses HTTP fast SSO first and falls back to browser-assisted login when the SSO form changes.",
    ),
    "tronclass": ProviderDefinition(
        key="tronclass",
        label="TronClass Public Cloud",
        base_url="https://www.tronclass.com.tw",
        login_url="https://www.tronclass.com.tw/login",
        auth_flow="public_cloud_email",
        status="ready",
        support_level="ready",
        capabilities=ProviderCapabilities(
            number=True,
            radar=True,
            qrcode=True,
            course_discovery=True,
            teacher_rollcall=True,
            manual_qr=True,
            local_scanner=True,
            direct_code_lookup=True,
        ),
        notes="Public TronClass cloud tenant. Uses the shared TronClass APIs after an email/password login form POST.",
    ),
    "nsysu": ProviderDefinition(
        key="nsysu",
        label="National Sun Yat-sen University TronClass",
        base_url="https://elearn.nsysu.edu.tw",
        login_url=(
            "https://identity.nsysu.edu.tw/auth/realms/nsysu/protocol/cas/login"
            "?ui_locales=zh-TW&service=https%3A//elearn.nsysu.edu.tw/login&locale=zh_TW"
        ),
        auth_flow="thu_cas",
        status="ready",
        support_level="ready",
        capabilities=ProviderCapabilities(
            number=True,
            radar=True,
            qrcode=True,
            course_discovery=True,
            teacher_rollcall=True,
            manual_qr=True,
            local_scanner=True,
            direct_code_lookup=True,
        ),
        notes="同 THU 的 WisdomGarden Keycloak CAS 登入（identity.nsysu.edu.tw realm nsysu），重用 thu_cas。登入導向已查證；尚未用真實帳號跑過端到端登入。",
    ),
    "cyut": ProviderDefinition(
        key="cyut",
        label="Chaoyang University of Technology TronClass",
        base_url="https://tronclass.cyut.edu.tw",
        login_url=(
            "https://tcidentity.cyut.edu.tw/auth/realms/cyut/protocol/cas/login"
            "?ui_locales=zh-TW&service=https%3A//tronclass.cyut.edu.tw/login&locale=zh_TW"
        ),
        auth_flow="thu_cas",
        status="ready",
        support_level="ready",
        capabilities=ProviderCapabilities(
            number=True,
            radar=True,
            qrcode=True,
            course_discovery=True,
            teacher_rollcall=True,
            manual_qr=True,
            local_scanner=True,
            direct_code_lookup=True,
        ),
        notes="同 THU 的 WisdomGarden Keycloak CAS 登入（tcidentity.cyut.edu.tw realm cyut），重用 thu_cas。登入導向已查證；尚未用真實帳號跑過端到端登入。",
    ),
    "ntou": ProviderDefinition(
        key="ntou",
        label="National Taiwan Ocean University TronClass",
        base_url="https://tronclass.ntou.edu.tw",
        login_url=(
            "https://tccas.ntou.edu.tw/cas/login"
            "?ui_locales=zh-TW&service=https%3A//tronclass.ntou.edu.tw/login&locale=zh_TW"
        ),
        auth_flow="tronclass_form_captcha",
        status="ready",
        support_level="ready",
        capabilities=ProviderCapabilities(
            number=True,
            radar=True,
            qrcode=True,
            course_discovery=True,
            teacher_rollcall=True,
            manual_qr=True,
            local_scanner=True,
            direct_code_lookup=True,
        ),
        notes="Apereo CAS + 圖形驗證碼（同 FJU，重用 tronclass_form_captcha；裝 .[ocr] 自動辨識、否則人工輸入）。CAS 主機 tccas.ntou.edu.tw 與 app 主機不同，故 login_url 直指 CAS。登入導向＋驗證碼頁已查證；尚未用真實帳號跑過端到端登入。",
    ),
}

PROVIDER_ALIASES = {
    "": DEFAULT_PROVIDER,
    "tunghai": "thu",
    "thu.edu": "thu",
    "ilearn": "thu",
    "ilearn.thu": "thu",
    "東海": "thu",
    "東海大學": "thu",
    "fju.edu": "fju",
    "輔仁": "fju",
    "輔仁大學": "fju",
    "tamkang": "tku",
    "淡江": "tku",
    "淡江大學": "tku",
    "tc": "tronclass",
    "tron": "tronclass",
    "tronclass": "tronclass",
    "tronclass.com": "tronclass",
    "tronclass.com.tw": "tronclass",
    "www.tronclass.com.tw": "tronclass",
    "官方": "tronclass",
    "官方站": "tronclass",
    "nsysu.edu": "nsysu",
    "elearn.nsysu": "nsysu",
    "中山": "nsysu",
    "中山大學": "nsysu",
    "國立中山大學": "nsysu",
    "cyut.edu": "cyut",
    "tronclass.cyut": "cyut",
    "朝陽": "cyut",
    "朝陽科大": "cyut",
    "朝陽科技大學": "cyut",
    "ntou.edu": "ntou",
    "tronclass.ntou": "ntou",
    "海大": "ntou",
    "海洋大學": "ntou",
    "臺灣海洋大學": "ntou",
    "台灣海洋大學": "ntou",
    "國立臺灣海洋大學": "ntou",
}


def normalize_provider_name(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    return PROVIDER_ALIASES.get(normalized, normalized or DEFAULT_PROVIDER)


def get_provider(name: Any = "") -> ProviderDefinition:
    key = normalize_provider_name(name)
    return PROVIDERS.get(key, PROVIDERS[DEFAULT_PROVIDER])


def list_providers() -> List[ProviderDefinition]:
    return [PROVIDERS[key] for key in sorted(PROVIDERS)]


def list_all_providers() -> List[ProviderDefinition]:
    return list_providers()


def list_supported_providers(include_hidden: bool = False) -> List[ProviderDefinition]:
    providers = list_all_providers()
    if include_hidden:
        return providers
    return [provider for provider in providers if provider.user_visible]


def provider_to_config(provider: ProviderDefinition) -> Dict[str, Any]:
    return provider.to_config()


def provider_registry_config() -> Dict[str, Any]:
    return {
        "current": DEFAULT_PROVIDER,
        # Back-compat no-op: provider maturity no longer gates user-level daily flow.
        "allow_experimental": False,
        "available": {
            key: provider.to_config()
            for key, provider in sorted(PROVIDERS.items())
        },
    }


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def provider_support_report(provider: Any, allow_experimental: bool = False) -> Dict[str, Any]:
    if hasattr(provider, "to_config"):
        config = provider.to_config()
    elif isinstance(provider, Mapping):
        config = dict(provider)
    else:
        config = get_provider(provider).to_config()

    capabilities = config.get("capabilities")
    if not isinstance(capabilities, Mapping):
        capabilities = {}
    support_level = str(config.get("support_level") or config.get("status") or "unsupported")
    endpoint_configured = {
        "base_url": bool(str(config.get("base_url") or "").strip()),
        "login_url": bool(str(config.get("login_url") or "").strip()),
        "rollcalls_url": bool(str(config.get("rollcalls_url") or "").strip()),
        "current_semester_url": bool(str(config.get("current_semester_url") or "").strip()),
        "courses_url": bool(str(config.get("courses_url") or "").strip()),
    }
    daily_ready = support_level == "ready"
    return {
        "key": str(config.get("key") or DEFAULT_PROVIDER),
        "label": str(config.get("label") or ""),
        "support_level": support_level,
        "status": str(config.get("status") or support_level),
        "ready": support_level == "ready",
        "daily_ready": daily_ready,
        "user_visible": bool(config.get("user_visible", True)),
        "allow_experimental": bool(allow_experimental),
        "endpoint_configured": endpoint_configured,
        "capabilities": dict(capabilities),
    }


def normalize_provider_config(value: Any) -> Dict[str, Any]:
    if isinstance(value, str):
        raw_config: Dict[str, Any] = {"current": value}
    elif isinstance(value, Mapping):
        raw_config = dict(value)
    else:
        raw_config = {}

    requested = raw_config.get("current", raw_config.get("name", raw_config.get("school", "")))
    requested_key = normalize_provider_name(requested)
    current = normalize_provider_name(requested)
    fallback_reason = ""
    if current not in PROVIDERS:
        fallback_reason = "unknown_provider"
        current = DEFAULT_PROVIDER

    available = raw_config.get("available")
    if not isinstance(available, Mapping):
        available = {}

    merged_available: Dict[str, Dict[str, Any]] = {}
    for key, provider in sorted(PROVIDERS.items()):
        merged = provider.to_config()
        override = available.get(key)
        if isinstance(override, Mapping):
            if "base_url" in override:
                merged["base_url"] = str(override["base_url"] or "")
                endpoints = tronclass_api_endpoints(merged["base_url"])
                merged["rollcalls_url"] = endpoints["rollcalls_url"]
                merged["current_semester_url"] = endpoints["current_semester_url"]
                merged["courses_url"] = endpoints["courses_url"]
            for override_key in (
                "login_url",
                "rollcalls_url",
                "current_semester_url",
                "courses_url",
                "auth_flow",
                "notes",
            ):
                if override_key in override:
                    merged[override_key] = str(override[override_key] or "")
            if "user_visible" in override:
                merged["user_visible"] = _coerce_bool(override.get("user_visible"), bool(merged.get("user_visible", True)))
        merged_available[key] = merged

    return {
        "current": current,
        "requested": requested_key or DEFAULT_PROVIDER,
        "fallback_reason": fallback_reason,
        "allow_experimental": _coerce_bool(raw_config.get("allow_experimental"), False),
        "available": merged_available,
    }
