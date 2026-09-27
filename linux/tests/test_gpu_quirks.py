"""Tests for the NVIDIA rendering workarounds."""

from doubao_murmur import gpu_quirks


def _marker(tmp_path, exists):
    path = tmp_path / "nvidia_version"
    if exists:
        path.write_text("NVRM version: 550.00\n")
    return (path,)


class TestApply:
    def test_sets_workarounds_on_nvidia(self, tmp_path):
        env = {}
        applied = gpu_quirks.apply(env, _marker(tmp_path, True))
        assert env == {
            "GSK_RENDERER": "cairo",
            "WEBKIT_DISABLE_DMABUF_RENDERER": "1",
        }
        assert applied == env

    def test_leaves_other_gpus_alone(self, tmp_path):
        env = {}
        assert gpu_quirks.apply(env, _marker(tmp_path, False)) == {}
        assert env == {}

    def test_user_setting_wins(self, tmp_path):
        env = {"GSK_RENDERER": "ngl"}
        applied = gpu_quirks.apply(env, _marker(tmp_path, True))
        assert env["GSK_RENDERER"] == "ngl"
        assert env["WEBKIT_DISABLE_DMABUF_RENDERER"] == "1"
        assert applied == {"WEBKIT_DISABLE_DMABUF_RENDERER": "1"}

    def test_empty_user_setting_is_respected(self, tmp_path):
        env = {"WEBKIT_DISABLE_DMABUF_RENDERER": ""}
        gpu_quirks.apply(env, _marker(tmp_path, True))
        assert env["WEBKIT_DISABLE_DMABUF_RENDERER"] == ""

    def test_any_marker_counts(self, tmp_path):
        missing = tmp_path / "missing"
        present = tmp_path / "present"
        present.write_text("x")
        assert gpu_quirks.has_nvidia_driver((missing, present))
        assert not gpu_quirks.has_nvidia_driver((missing,))
