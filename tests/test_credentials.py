"""凭据（账号密码）安全存储测试。"""

from __future__ import annotations

import unittest
from pathlib import Path

from campus_login import credman, dpapi
from campus_login.credentials import (
    Credential,
    DpapiCredentialStore,
    MemoryCredentialStore,
    WindowsCredentialManagerStore,
    create_store,
    save_credential,
)

from .support import TempDataDirTestCase

SECRET = "Test-Password-1234!"


class CredentialModelTest(unittest.TestCase):
    def test_repr_never_exposes_password(self):
        credential = Credential("2021001", SECRET)
        self.assertNotIn(SECRET, repr(credential))
        self.assertNotIn(SECRET, str(credential))

    def test_redact_replaces_password(self):
        credential = Credential("2021001", SECRET)
        text = f"登录失败 password={SECRET} 请检查"
        self.assertNotIn(SECRET, credential.redact(text))

    def test_complete_flag(self):
        self.assertFalse(Credential("", "").complete)
        self.assertFalse(Credential("user", "").complete)
        self.assertTrue(Credential("user", "pass").complete)


class MemoryStoreTest(TempDataDirTestCase):
    def test_round_trip_and_delete(self):
        store = MemoryCredentialStore()
        self.assertIsNone(store.load())
        store.save(Credential("2021001", SECRET))
        loaded = store.load()
        self.assertEqual(loaded.username, "2021001")
        self.assertEqual(loaded.password, SECRET)
        self.assertTrue(store.delete())
        self.assertIsNone(store.load())


class DpapiStoreTest(TempDataDirTestCase):
    @unittest.skipUnless(dpapi.available(), "DPAPI 仅在 Windows 可用")
    def test_dpapi_round_trip(self):
        store = DpapiCredentialStore()
        self.assertIsNone(store.load())
        store.save(Credential("2021001", SECRET))
        loaded = store.load()
        self.assertEqual(loaded.username, "2021001")
        self.assertEqual(loaded.password, SECRET)
        self.assertTrue(store.delete())
        self.assertIsNone(store.load())

    @unittest.skipUnless(dpapi.available(), "DPAPI 仅在 Windows 可用")
    def test_encrypted_file_does_not_contain_plaintext(self):
        store = DpapiCredentialStore()
        store.save(Credential("2021001", SECRET))
        raw = store.path.read_bytes()
        self.assertNotIn(SECRET.encode("utf-8"), raw)
        self.assertNotIn(b"2021001", raw)

    @unittest.skipUnless(dpapi.available(), "DPAPI 仅在 Windows 可用")
    def test_corrupted_file_returns_none(self):
        store = DpapiCredentialStore()
        store.path.parent.mkdir(parents=True, exist_ok=True)
        store.path.write_bytes(b"not-a-valid-dpapi-blob")
        self.assertIsNone(store.load())

    def test_dpapi_protect_unprotect_bytes(self):
        if not dpapi.available():
            self.skipTest("DPAPI 仅在 Windows 可用")
        blob = dpapi.protect("中文密码".encode("utf-8"), b"entropy")
        self.assertNotIn("中文密码".encode("utf-8"), blob)
        self.assertEqual(dpapi.unprotect(blob, b"entropy").decode("utf-8"), "中文密码")


class CredentialManagerStoreTest(TempDataDirTestCase):
    """使用独立的凭据条目名做真实读写，结束后立即删除。"""

    target = "CampusLogin/Tests"

    @unittest.skipUnless(credman.available(), "Windows 凭据管理器仅在 Windows 可用")
    def test_round_trip_and_delete(self):
        store = WindowsCredentialManagerStore(self.target)
        store.delete()
        self.addCleanup(store.delete)
        self.assertIsNone(store.load())
        store.save(Credential("2021001", SECRET))
        loaded = store.load()
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.username, "2021001")
        self.assertEqual(loaded.password, SECRET)
        self.assertTrue(store.delete())
        self.assertIsNone(store.load())


class StoreFactoryTest(TempDataDirTestCase):
    def test_auto_backend_is_usable(self):
        store, description = create_store("auto")
        self.assertIn(store.name, ("credman", "dpapi", "memory"))
        self.assertTrue(description)

    def test_memory_backend(self):
        store, _ = create_store("memory")
        self.assertEqual(store.name, "memory")

    def test_save_credential_returns_working_store(self):
        store, _ = create_store("auto")
        used = save_credential(store, Credential("2021001", SECRET))
        loaded = used.load()
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.password, SECRET)
        used.delete()

    def test_dpapi_backend_selection(self):
        if not dpapi.available():
            self.skipTest("DPAPI 仅在 Windows 可用")
        store, _ = create_store("dpapi")
        self.assertIsInstance(store, DpapiCredentialStore)
        store.save(Credential("2021001", SECRET))
        self.assertTrue(Path(store.path).exists())


if __name__ == "__main__":
    unittest.main()
