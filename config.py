"""iMessage 适配器配置模型（WebUI 配置页由此自动生成）。"""

from __future__ import annotations

from typing import ClassVar, Dict, Literal, Optional

from maibot_sdk import Field, PluginConfigBase

# 配置结构有增减时提升版本：MaiBot 只在版本升高时把新字段写回已有的 config.toml
SUPPORTED_CONFIG_VERSION = "2.5.0"
PLUGIN_VERSION = "0.2.2"

DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_INTERVAL = 3.0
DEFAULT_MAX_ATTACHMENT_SIZE_MB = 50
DEFAULT_HANDLE_CACHE_SIZE = 500
DEFAULT_IMAGE_MAX_EDGE = 2048


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
    bot_number: str = Field(
        default="",
        description="Photon 分配给你的项目的 iMessage 号码（对方发消息的那个号码），带国家代码。",
        json_schema_extra={
            "hint": (
                "在 Photon 控制台 (app.photon.codes) 能看到。适配器会在连接成功时把它作为机器人账号上报给 MaiBot。"
                "MaiBot 1.3.6 及以后的版本必须填写，否则收不到消息。注意：首次填写或更改后，MaiBot 会把这个 iMessage 对话当作一个新的聊天流。"
            ),
            "i18n": _schema_i18n(
                label_en="Photon number",
                label_ja="Photon の番号",
                label_ko="Photon 번호",
                hint_en="The iMessage number Photon assigned to your project, with country code. Reported to MaiBot as the bot account. Required on MaiBot 1.3.6 and later, otherwise no messages are received.",
                hint_ja="Photon がプロジェクトに割り当てた iMessage 番号（国番号付き）。ボットのアカウントとして MaiBot に報告されます。MaiBot 1.3.6 以降は必須で、未入力だとメッセージを受信できません。",
                hint_ko="Photon이 프로젝트에 할당한 iMessage 번호(국가 코드 포함). 봇 계정으로 MaiBot에 보고됩니다. MaiBot 1.3.6 이상에서는 필수이며, 입력하지 않으면 메시지를 받을 수 없습니다.",
                placeholder_en="+14155550100",
                placeholder_ja="+14155550100",
                placeholder_ko="+14155550100",
            ),
            "label": "Photon 号码",
            "order": 2,
            "placeholder": "+14155550100",
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
    recover_missed: bool = Field(
        default=True,
        description="每 2 分钟对一下 Photon 上最近对方发的消息，Photon 没推送过来的自动补收，照常交给 AI（会晚到几分钟）。只补适配器这次启动之后的消息。",
        json_schema_extra={
            "hint": "真机发现 Photon 断线恢复后偶尔会漏推几条消息。关闭后漏掉的消息不会补收；发 /imessage_missed 可以手动查。",
            "i18n": _schema_i18n(
                label_en="Recover missed messages",
                label_ja="取りこぼしたメッセージを回収",
                label_ko="누락된 메시지 복구",
                hint_en="Every 2 minutes, compare recent incoming messages on Photon and deliver the ones its live push missed.",
                hint_ja="2 分ごとに Photon 上の最近の受信メッセージと照合し、プッシュされなかったものを届けます。",
                hint_ko="2분마다 Photon의 최근 수신 메시지와 대조해 푸시되지 않은 메시지를 전달합니다.",
            ),
            "label": "漏消息自动补收",
            "order": 6,
        },
    )


class CapabilitiesConfig(PluginConfigBase):
    """iMessage 能力开关。关闭的能力不会出现在给 AI 的说明和工具列表里。"""

    __ui_label__: ClassVar[str] = "iMessage 能力"
    __ui_order__: ClassVar[int] = 3

    typing_indicator: bool = Field(
        default=True,
        description="AI 开始写回复时，对方手机上显示「正在输入」，发出后消失。AI 决定不回复时不会显示。",
        json_schema_extra={
            "i18n": _schema_i18n(label_en="Typing indicator", label_ja="入力中表示", label_ko="입력 중 표시"),
            "label": "输入中提示",
            "order": 0,
        },
    )
    read_receipts: bool = Field(
        default=True,
        description="开始处理对方消息时，自动给对方发「已读」。iMessage 会把整个对话一起标为已读。",
        json_schema_extra={
            "i18n": _schema_i18n(label_en="Send read receipts", label_ja="既読を送信", label_ko="읽음 확인 보내기"),
            "label": "自动发已读",
            "order": 1,
        },
    )
    reactions: bool = Field(
        default=True,
        description="允许 AI 给对方的消息点爱心、点赞等（iMessage Tapback）。",
        json_schema_extra={
            "i18n": _schema_i18n(label_en="Tapback reactions", label_ja="Tapback リアクション", label_ko="Tapback 반응"),
            "label": "点赞回应",
            "order": 2,
        },
    )
    edit_unsend: bool = Field(
        default=True,
        description="允许 AI 修改或撤回自己刚发出的消息（iMessage 限制：15 分钟内可改，2 分钟内可撤回），也能撤回自己点过的回应。",
        json_schema_extra={
            "i18n": _schema_i18n(label_en="Edit & unsend", label_ja="編集と取り消し", label_ko="편집 및 전송 취소"),
            "label": "编辑与撤回",
            "order": 6,
        },
    )
    effects: bool = Field(
        default=True,
        description="允许 AI 在特别的时刻（生日、祝贺等）发带特效的消息，例如气球、烟花、隐形墨水。",
        json_schema_extra={
            "i18n": _schema_i18n(label_en="Message effects", label_ja="メッセージエフェクト", label_ko="메시지 효과"),
            "label": "消息特效",
            "order": 7,
        },
    )
    polls: bool = Field(
        default=True,
        description="允许 AI 发起投票，让你在几个选项里点选。",
        json_schema_extra={
            "i18n": _schema_i18n(label_en="Polls", label_ja="投票", label_ko="투표"),
            "label": "投票",
            "order": 8,
        },
    )
    chat_background: bool = Field(
        default=True,
        description="你要求时，AI 可以把你发的某张图片设为这个对话的聊天背景，或清除背景。",
        json_schema_extra={
            "i18n": _schema_i18n(label_en="Chat background", label_ja="チャット背景", label_ko="채팅 배경"),
            "label": "聊天背景",
            "order": 9,
        },
    )
    stickers: bool = Field(
        default=True,
        description="允许 AI 从 MaiBot 的表情包库里挑一张，当作贴纸贴在你的某条消息上。",
        json_schema_extra={
            "i18n": _schema_i18n(label_en="Place stickers", label_ja="ステッカーを貼る", label_ko="스티커 붙이기"),
            "label": "贴贴纸",
            "order": 15,
        },
    )
    share_contact: bool = Field(
        default=True,
        description="你要 AI 的联系方式时，AI 可以用 iMessage「共享名字与照片」把机器人账号的名字和头像发给你。",
        json_schema_extra={
            "i18n": _schema_i18n(label_en="Share contact card", label_ja="連絡先カードを共有", label_ko="연락처 카드 공유"),
            "label": "分享名片",
            "order": 10,
        },
    )
    link_preview: bool = Field(
        default=True,
        description="AI 的一段回复只有一个链接时，以链接预览卡片发出（显示网页标题和图片）。",
        json_schema_extra={
            "i18n": _schema_i18n(label_en="Link previews", label_ja="リンクプレビュー", label_ko="링크 미리보기"),
            "label": "链接预览",
            "order": 11,
        },
    )
    notify_reactions: bool = Field(
        default=True,
        description="对方给消息点赞（或取消点赞）、在投票里选了选项时，告诉 AI（AI 在下一轮能看到）。",
        json_schema_extra={
            "i18n": _schema_i18n(label_en="Tell the AI about tapbacks and votes", label_ja="相手のリアクションと投票を AI に伝える", label_ko="상대의 반응과 투표를 AI에게 알림"),
            "label": "告诉 AI 对方的点赞和投票",
            "order": 12,
        },
    )
    notify_edits: bool = Field(
        default=True,
        description="对方编辑或撤回自己的消息时告诉 AI，并附上原来那句（如果还记得）。",
        json_schema_extra={
            "i18n": _schema_i18n(label_en="Tell the AI about edits and unsends", label_ja="編集と取り消しを AI に伝える", label_ko="편집 및 전송 취소를 AI에게 알림"),
            "label": "告诉 AI 对方的编辑和撤回",
            "order": 14,
        },
    )
    notify_read: bool = Field(
        default=True,
        description="对方读了 AI 发的消息时告诉 AI。同一批消息只提醒一次。",
        json_schema_extra={
            "i18n": _schema_i18n(label_en="Tell the AI about read receipts", label_ja="既読を AI に伝える", label_ko="읽음 확인을 AI에게 알림"),
            "label": "告诉 AI 对方已读",
            "order": 13,
        },
    )
    reply_mode: Literal["auto", "always", "off"] = Field(
        default="auto",
        description="AI 的回复要不要以「引用对方某句话」的形式发出。auto：交给 MaiBot（1.3.9 及以前由 AI 自己决定；1.3.10 起按 MaiBot 设置里的「私聊引用频率」）；always：总是引用对方最新一句；off：从不引用。",
        json_schema_extra={
            "i18n": _schema_i18n(
                label_en="Quote replies",
                label_ja="引用返信",
                label_ko="인용 답장",
                hint_en="auto: left to MaiBot (the AI up to 1.3.9; the private quote frequency setting from 1.3.10); always: always quote the latest message; off: never quote.",
                hint_ja="auto：MaiBot に任せる（1.3.9 までは AI が判断、1.3.10 からは個人チャットの引用返信頻度の設定）／always：常に最新の発言を引用／off：引用しない。",
                hint_ko="auto: MaiBot에 맡김 (1.3.9까지는 AI가 결정, 1.3.10부터는 개인 채팅 인용 빈도 설정) / always: 항상 최신 메시지 인용 / off: 인용 안 함.",
            ),
            "label": "引用回复",
            "order": 3,
        },
    )
    read_text_files: bool = Field(
        default=True,
        description="对方发来 .txt、.md、.json 等文本文件（256KB 以内）时，把文件内容交给 AI（最多 6000 字）。关闭则只告诉 AI 文件名。",
        json_schema_extra={
            "i18n": _schema_i18n(label_en="Read text files", label_ja="テキストファイルを読む", label_ko="텍스트 파일 읽기"),
            "label": "读取文本文件",
            "order": 5,
        },
    )
    markdown: bool = Field(
        default=True,
        description="AI 回复里带 Markdown 格式（粗体、列表等）时，在 iMessage 里显示成对应样式；普通文字不受影响。",
        json_schema_extra={
            "i18n": _schema_i18n(label_en="Render Markdown", label_ja="Markdown を表示", label_ko="Markdown 표시"),
            "label": "Markdown 渲染",
            "order": 4,
        },
    )


class MediaConfig(PluginConfigBase):
    """收到图片时的处理方式。"""

    __ui_label__: ClassVar[str] = "图片处理"
    __ui_order__: ClassVar[int] = 4

    image_max_edge: int = Field(
        default=DEFAULT_IMAGE_MAX_EDGE,
        description="收到的图片交给 AI 之前，长边超过多少像素就缩小。填 0 表示不缩小。",
        json_schema_extra={
            "hint": "iPhone 的 HEIC 照片会统一转成 JPEG。识图用不上原图的上千万像素，缩小能省内存和模型流量。",
            "i18n": _schema_i18n(
                label_en="Max image edge (px)",
                label_ja="画像の最大辺（px）",
                label_ko="이미지 최대 변(px)",
                hint_en="HEIC photos are always converted to JPEG. 0 disables downscaling.",
                hint_ja="HEIC 写真は常に JPEG に変換されます。0 で縮小しません。",
                hint_ko="HEIC 사진은 항상 JPEG로 변환됩니다. 0이면 축소하지 않습니다.",
            ),
            "label": "图片最大边长（像素）",
            "order": 0,
        },
    )


class VoiceConfig(PluginConfigBase):
    """语音消息。"""

    __ui_label__: ClassVar[str] = "语音"
    __ui_order__: ClassVar[int] = 5

    transcribe: bool = Field(
        default=False,
        description="收到语音时，用 MaiBot「模型配置」里的语音识别任务把它转成文字交给 AI。关闭时（或识别失败时），AI 会得体地告诉对方暂时听不了语音。",
        json_schema_extra={
            "hint": "需要先在 MaiBot 里配置语音识别模型。iPhone 的语音是 CAF 格式，识别服务不一定支持，开启前请先确认。",
            "i18n": _schema_i18n(
                label_en="Transcribe voice messages",
                label_ja="音声メッセージを文字起こし",
                label_ko="음성 메시지 받아쓰기",
                hint_en="Requires a speech-recognition model in MaiBot. iPhone voice notes are CAF files; check that your service accepts them.",
                hint_ja="MaiBot で音声認識モデルの設定が必要です。iPhone の音声は CAF 形式です。",
                hint_ko="MaiBot에 음성 인식 모델이 설정되어 있어야 합니다. iPhone 음성은 CAF 형식입니다.",
            ),
            "label": "语音转文字",
            "order": 0,
        },
    )
    tts_service: str = Field(
        default="",
        description="预留位置：以后接入语音合成（让 AI 发语音）时，在这里填写服务地址。当前版本不会使用，AI 也看不到发语音的能力。",
        json_schema_extra={
            "i18n": _schema_i18n(
                label_en="Text-to-speech service (reserved)",
                label_ja="音声合成サービス（予約）",
                label_ko="음성 합성 서비스(예약)",
            ),
            "label": "语音合成服务（预留）",
            "order": 1,
        },
    )


class IMessageAdapterConfig(PluginConfigBase):
    """iMessage 适配器插件完整配置。"""

    plugin: IMessagePluginOptions = Field(default_factory=IMessagePluginOptions)
    photon: PhotonServerConfig = Field(default_factory=PhotonServerConfig)
    bridge: SidecarBridgeConfig = Field(default_factory=SidecarBridgeConfig)
    capabilities: CapabilitiesConfig = Field(default_factory=CapabilitiesConfig)
    media: MediaConfig = Field(default_factory=MediaConfig)
    voice: VoiceConfig = Field(default_factory=VoiceConfig)
