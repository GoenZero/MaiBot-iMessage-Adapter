"""iMessage 适配器配置模型（WebUI 配置页由此自动生成）。"""

from __future__ import annotations

from typing import ClassVar, Dict, Optional

from maibot_sdk import Field, PluginConfigBase

SUPPORTED_CONFIG_VERSION = "2.0.0"
PLUGIN_VERSION = "0.2.0"

DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_INTERVAL = 3.0
DEFAULT_MAX_ATTACHMENT_SIZE_MB = 50
DEFAULT_HANDLE_CACHE_SIZE = 500


def _schema_i18n(
    *,
    label_en: str,
    label_ja: str,
    label_ko: str,
    hint_en: Optional[str] = None,
    hint_ja: Optional[str] = None,
    hint_ko: Optional[str] = None,
    placeholder_en: Optional[str] = None,
    placeholder_ja: Optional[str] = None,
    placeholder_ko: Optional[str] = None,
) -> Dict[str, Dict[str, str]]:
    """构造 WebUI 配置项的多语言说明文本。"""

    i18n: Dict[str, Dict[str, str]] = {
        "en_US": {"label": label_en},
        "ja_JP": {"label": label_ja},
        "ko_KR": {"label": label_ko},
    }
    if hint_en is not None:
        i18n["en_US"]["hint"] = hint_en
    if hint_ja is not None:
        i18n["ja_JP"]["hint"] = hint_ja
    if hint_ko is not None:
        i18n["ko_KR"]["hint"] = hint_ko
    if placeholder_en is not None:
        i18n["en_US"]["placeholder"] = placeholder_en
    if placeholder_ja is not None:
        i18n["ja_JP"]["placeholder"] = placeholder_ja
    if placeholder_ko is not None:
        i18n["ko_KR"]["placeholder"] = placeholder_ko
    return i18n


class IMessagePluginOptions(PluginConfigBase):
    """插件级配置。"""

    __ui_label__: ClassVar[str] = "插件设置"
    __ui_order__: ClassVar[int] = 0

    enabled: bool = Field(
        default=False,
        description="是否启用 iMessage 适配器。",
        json_schema_extra={
            "hint": "关闭后插件会保持空闲，不会启动侧车进程或连接 Photon。",
            "i18n": _schema_i18n(
                label_en="Enable adapter",
                label_ja="アダプターを有効化",
                label_ko="어댑터 활성화",
                hint_en="When disabled, the plugin stays idle and will not launch the sidecar process or connect to Photon.",
                hint_ja="無効にすると、プラグインは待機状態のままになり、サイドカープロセスを起動せず、Photon にも接続しません。",
                hint_ko="비활성화하면 플러그인이 대기 상태로 유지되며 사이드카 프로세스를 시작하지 않고 Photon에 연결하지 않습니다.",
            ),
            "label": "启用适配器",
            "order": 0,
        },
    )
    config_version: str = Field(
        default=SUPPORTED_CONFIG_VERSION,
        description="当前配置结构版本。",
        json_schema_extra={
            "disabled": True,
            "hidden": True,
            "i18n": _schema_i18n(label_en="Config version", label_ja="設定バージョン", label_ko="설정 버전"),
            "label": "配置版本",
            "order": 99,
        },
    )

    def should_connect(self) -> bool:
        """判断当前配置下是否应当启动连接。"""
        return self.enabled


class PhotonServerConfig(PluginConfigBase):
    """Photon Spectrum 云端连接配置。"""

    __ui_label__: ClassVar[str] = "Photon 云端"
    __ui_order__: ClassVar[int] = 1

    project_id: str = Field(
        default="",
        description="Photon 项目 ID，用于标识你的 Photon 项目。",
        json_schema_extra={
            "hint": "在 Photon 控制台 (app.photon.codes) 创建项目后获取。",
            "i18n": _schema_i18n(
                label_en="Project ID",
                label_ja="プロジェクト ID",
                label_ko="프로젝트 ID",
                hint_en="Obtain from the Photon dashboard (app.photon.codes) after creating a project.",
                hint_ja="Photon ダッシュボード (app.photon.codes) でプロジェクト作成後に取得します。",
                hint_ko="Photon 대시보드(app.photon.codes)에서 프로젝트 생성 후 확인할 수 있습니다.",
                placeholder_en="PROJECT_ID",
                placeholder_ja="PROJECT_ID",
                placeholder_ko="PROJECT_ID",
            ),
            "label": "项目 ID",
            "order": 0,
            "placeholder": "PROJECT_ID",
        },
    )
    project_secret: str = Field(
        default="",
        description="Photon 项目密钥，用于认证你的 Photon 项目身份。",
        json_schema_extra={
            "hint": "与项目 ID 配套的密钥，请妥善保管，切勿泄露。",
            "i18n": _schema_i18n(
                label_en="Project secret",
                label_ja="プロジェクトシークレット",
                label_ko="프로젝트 시크릿",
                hint_en="The secret paired with the project ID. Keep it safe and never expose it.",
                hint_ja="プロジェクト ID とペアになるシークレットです。安全に保管し、決して公開しないでください。",
                hint_ko="프로젝트 ID와 페어링되는 시크릿입니다. 안전하게 보관하고 절대 공개하지 마십시오.",
                placeholder_en="PROJECT_SECRET",
                placeholder_ja="PROJECT_SECRET",
                placeholder_ko="PROJECT_SECRET",
            ),
            "input_type": "password",
            "label": "项目密钥",
            "order": 1,
            "placeholder": "PROJECT_SECRET",
        },
    )


class SidecarBridgeConfig(PluginConfigBase):
    """本地 Node.js 侧车配置。"""

    __ui_label__: ClassVar[str] = "本地侧车"
    __ui_order__: ClassVar[int] = 2

    max_retries: int = Field(
        default=DEFAULT_MAX_RETRIES,
        description="侧车异常退出后，按「重启间隔」快速重试的次数；之后改为逐渐拉长间隔（最长 5 分钟）继续重试，不会放弃。",
        json_schema_extra={
            "hint": "只有 Photon 认证失败时才会停止重试；修改项目 ID 或密钥后会自动重连。",
            "i18n": _schema_i18n(
                label_en="Fast retries",
                label_ja="高速リトライ回数",
                label_ko="빠른 재시도 횟수",
                hint_en="After these quick retries the interval grows up to 5 minutes; retrying stops only on Photon authentication failure.",
                hint_ja="この回数の後は間隔を最大 5 分まで延ばして再試行を続けます。Photon 認証失敗時のみ停止します。",
                hint_ko="이 횟수 이후에는 간격을 최대 5분까지 늘려 계속 재시도합니다. Photon 인증 실패 시에만 중지합니다.",
            ),
            "label": "快速重试次数",
            "order": 1,
        },
    )
    retry_interval: float = Field(
        default=DEFAULT_RETRY_INTERVAL,
        description="侧车异常退出后，等待多少秒再尝试重启。",
        json_schema_extra={
            "hint": "建议不低于 2 秒，给 Photon 服务端断开检测留出时间。",
            "i18n": _schema_i18n(
                label_en="Retry interval (sec)",
                label_ja="リトライ間隔（秒）",
                label_ko="재시도 간격(초)",
                hint_en="Suggested at least 2 seconds to allow Photon to detect the disconnect.",
                hint_ja="Photon サーバー側の切断検出のため、2 秒以上を推奨します。",
                hint_ko="Photon 서버 측의 연결 해제 감지를 위해 2초 이상을 권장합니다.",
            ),
            "label": "重启间隔（秒）",
            "order": 2,
            "step": 0.5,
        },
    )
    max_attachment_size_mb: int = Field(
        default=DEFAULT_MAX_ATTACHMENT_SIZE_MB,
        description="单个附件的大小上限（MB），收发都适用。超过上限的附件不会下载，会告诉 AI「文件太大没接收」。",
        json_schema_extra={
            "hint": "附件通过本机临时文件传递，不再受内部连接大小限制。iMessage 本身单个附件最大约 100MB。",
            "i18n": _schema_i18n(
                label_en="Max attachment size (MB)",
                label_ja="最大添付ファイルサイズ (MB)",
                label_ko="최대 첨부 파일 크기(MB)",
                hint_en="Attachments are exchanged through local temp files. iMessage itself allows about 100 MB per attachment.",
                hint_ja="添付ファイルはローカルの一時ファイルで受け渡します。iMessage 自体の上限は約 100MB です。",
                hint_ko="첨부 파일은 로컬 임시 파일로 전달됩니다. iMessage 자체 한도는 약 100MB입니다.",
            ),
            "label": "附件大小上限（MB）",
            "order": 3,
        },
    )
    handle_cache_size: int = Field(
        default=DEFAULT_HANDLE_CACHE_SIZE,
        description="侧车记住最近多少条消息，供引用、点赞、撤回、编辑使用。",
        json_schema_extra={
            "hint": "重启后会清空；更早的消息侧车会尝试向 Photon 查询。",
            "i18n": _schema_i18n(
                label_en="Recent message cache",
                label_ja="最近のメッセージキャッシュ",
                label_ko="최근 메시지 캐시",
                hint_en="Cleared on restart; older messages are looked up from Photon when possible.",
                hint_ja="再起動でクリアされます。古いメッセージは可能なら Photon から検索します。",
                hint_ko="재시작 시 초기화됩니다. 오래된 메시지는 가능하면 Photon에서 조회합니다.",
            ),
            "label": "消息缓存条数",
            "order": 4,
        },
    )
    node_path: str = Field(
        default="",
        description="Node.js 程序的完整路径。留空则自动查找（系统 → 插件目录 → 自动下载）。",
        json_schema_extra={
            "hint": "插件目录所在磁盘不保留可执行权限时（例如 fnOS 数据盘），可把 node 放到别处并在这里填写路径。需要 Node.js 22 或以上。",
            "i18n": _schema_i18n(
                label_en="Node.js path",
                label_ja="Node.js のパス",
                label_ko="Node.js 경로",
                hint_en="Leave empty to auto-detect. Useful when the plugin disk does not keep executable bits. Node.js 22+ required.",
                hint_ja="空欄なら自動検出します。プラグインのディスクが実行権限を保持しない場合に指定します。Node.js 22 以上が必要です。",
                hint_ko="비워 두면 자동으로 찾습니다. 플러그인 디스크가 실행 권한을 유지하지 않을 때 지정하십시오. Node.js 22 이상 필요.",
                placeholder_en="/usr/local/bin/node",
                placeholder_ja="/usr/local/bin/node",
                placeholder_ko="/usr/local/bin/node",
            ),
            "label": "Node.js 路径（可选）",
            "order": 5,
            "placeholder": "/usr/local/bin/node",
        },
    )


class IMessageAdapterConfig(PluginConfigBase):
    """iMessage 适配器插件完整配置。"""

    plugin: IMessagePluginOptions = Field(default_factory=IMessagePluginOptions)
    photon: PhotonServerConfig = Field(default_factory=PhotonServerConfig)
    bridge: SidecarBridgeConfig = Field(default_factory=SidecarBridgeConfig)
