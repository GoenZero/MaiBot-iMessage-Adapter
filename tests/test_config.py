"""旧版（0.1.x）配置升级到新结构时，用户填过的值要保留。"""

from __future__ import annotations

from maibot_sdk.config import rebuild_plugin_config_data

import imessage_adapter_under_test as plugin_module


def test_old_config_upgrade_keeps_credentials() -> None:
    old = {
        "plugin": {"enabled": True, "config_version": "1.0.0", "plugin_version": "0.1.5"},
        "photon": {"project_id": "pid", "project_secret": "sec"},
        "bridge": {"ws_port": 18763, "max_retries": 5, "retry_interval": 3.0, "max_attachment_size_mb": 10},
    }
    plugin = plugin_module.create_plugin()
    default = plugin.build_default_config()
    rebuilt = rebuild_plugin_config_data(default, old)
    normalized, _ = plugin.normalize_plugin_config(rebuilt)
    assert normalized["photon"] == {"project_id": "pid", "project_secret": "sec"}
    assert normalized["plugin"]["enabled"] is True
    assert "ws_port" not in normalized["bridge"]
    assert "plugin_version" not in normalized["plugin"]
    assert normalized["bridge"]["max_retries"] == 5
    assert normalized["bridge"]["handle_cache_size"] == 500
