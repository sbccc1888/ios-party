"""Exercise the actual peripheral preparation function with host EFI doubles."""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
PRELUDE = r"""
#include <assert.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef uint64_t EFI_STATUS;
typedef uintptr_t UINTN;
typedef uint64_t UINT64;
typedef uint32_t UINT32;
typedef uint8_t UINT8;
typedef void VOID;
typedef void *EFI_HANDLE;
typedef struct { uint64_t words[2]; } EFI_GUID;
#define STATIC static
#define EFIAPI
#define FALSE 0
#define EFI_NATIVE_INTERFACE 0
#define OFFSET_OF offsetof
#define STATIC_ASSERT _Static_assert
#define EFI_SUCCESS UINT64_C(0)
#define EFI_UNSUPPORTED UINT64_C(0x8000000000000003)
#define EFI_DEVICE_ERROR UINT64_C(0x8000000000000007)
#define EFI_OUT_OF_RESOURCES UINT64_C(0x8000000000000009)
#define EFI_NOT_FOUND UINT64_C(0x800000000000000e)
#define EFI_ERROR(x) (((x) >> 63) != 0)
static EFI_GUID mUsbConfigFileGuid, mUsbConfigGuid, mUsbfnGuid;
static EFI_GUID gEfiLoadedImageProtocolGuid;
typedef struct { VOID *ImageBase; UINT64 ImageSize; } EFI_LOADED_IMAGE_PROTOCOL;
typedef struct BINDING BINDING;
struct BINDING {
  EFI_STATUS (*Supported)(BINDING *, EFI_HANDLE, VOID *);
  EFI_HANDLE ImageHandle;
};
typedef BINDING EFI_DRIVER_BINDING_PROTOCOL;
typedef struct {
  EFI_STATUS (*HandleProtocol)(EFI_HANDLE, EFI_GUID *, VOID **);
  EFI_STATUS (*InstallProtocolInterface)(EFI_HANDLE *, EFI_GUID *, int, VOID *);
  EFI_STATUS (*ConnectController)(EFI_HANDLE, EFI_HANDLE *, VOID *, int);
} BOOT_SERVICES;
static BOOT_SERVICES *gBS;
static int scenario, phase, allocations, frees, stops, provider_checks;
static VOID *published, *allocation;
static EFI_LOADED_IMAGE_PROTOCOL loaded;
static EFI_STATUS FindFirmwareImage(EFI_GUID *guid, EFI_HANDLE *handle) {
  assert(guid == &mUsbConfigFileGuid);
  if (scenario == 10) return EFI_NOT_FOUND;
  *handle = (VOID *)1;
  return EFI_SUCCESS;
}
static VOID Print(const VOID *format, ...) { (void)format; }
static VOID *AllocateCopyPool(UINTN size, const VOID *source) {
  allocations++;
  if (scenario == 3) return NULL;
  allocation = malloc(size);
  assert(allocation);
  return memcpy(allocation, source, size);
}
static VOID FreePool(VOID *pointer) {
  assert(pointer == allocation);
  frees++;
  free(pointer);
  allocation = NULL;
}
"""

DOUBLES = r"""
static QCOM_USB_CONFIG_INFO original, snapshot;
static EFI_STATUS stop_host(QCOM_USB_INIT *self, UINT8 core, UINT32 mode) {
  (void)self;
  assert(core == 0 && mode == 1 && phase == 0);
  stops++;
  phase = 1;
  /* The vendor may update its original public instance during StopController. */
  original.ModeType = 0x10000;
  return scenario == 4 ? EFI_DEVICE_ERROR : EFI_SUCCESS;
}
static EFI_STATUS get_protocol(EFI_HANDLE handle, EFI_GUID *guid, VOID **value) {
  if (handle == (VOID *)1) {
    assert(guid == &gEfiLoadedImageProtocolGuid);
    *value = &loaded;
    return EFI_SUCCESS;
  }
  assert(handle == (VOID *)2 && guid == &mUsbfnGuid && phase == 4);
  provider_checks++;
  if (scenario == 8) return EFI_NOT_FOUND;
  *value = (VOID *)3;
  return EFI_SUCCESS;
}
static EFI_STATUS install(EFI_HANDLE *handle, EFI_GUID *guid, int type, VOID *value) {
  QCOM_USB_CONFIG_INFO *config = value;
  assert(phase == 1 && guid == &mUsbConfigGuid && type == EFI_NATIVE_INTERFACE);
  assert(value != &original && config->CoreNum == 0 && config->ModeType == 4);
  assert(memcmp(config->Methods, snapshot.Methods, sizeof(config->Methods)) == 0);
  assert(config->GetVbusStatus == snapshot.GetVbusStatus);
  assert(config->EnableVbus == snapshot.EnableVbus);
  if (scenario == 5) return EFI_DEVICE_ERROR;
  *handle = (VOID *)2;
  published = value;
  phase = 2;
  return EFI_SUCCESS;
}
static EFI_STATUS supported(BINDING *self, EFI_HANDLE handle, VOID *path) {
  (void)self;
  assert(phase == 2 && handle == (VOID *)2 && path == NULL);
  if (scenario == 6) return EFI_UNSUPPORTED;
  phase = 3;
  return EFI_SUCCESS;
}
static EFI_STATUS connect(EFI_HANDLE handle, EFI_HANDLE *drivers, VOID *path, int recurse) {
  assert(phase == 3 && handle == (VOID *)2);
  assert(drivers[0] == (VOID *)0x44 && drivers[1] == NULL);
  assert(path == NULL && recurse == 0);
  if (scenario == 7) return EFI_DEVICE_ERROR;
  phase = 4;
  return EFI_SUCCESS;
}
"""

MAIN = r"""
int main(int argc, char **argv) {
  assert(argc == 2);
  scenario = atoi(argv[1]);
  original.Revision = scenario == 1 ? 0x10004 : 0x10003;
  original.CoreNum = 0;
  original.ModeType = 1;
  for (UINTN i = 0; i < 16; i++) original.Methods[i] = (VOID *)(0x10001000 + i * 4);
  original.Methods[0] = (VOID *)0x10001440;
  original.Methods[1] = (VOID *)(UINTN)(scenario == 2 ? 0x10001518 : 0x10001514);
  original.Methods[4] = (VOID *)0x10001750;
  original.GetVbusStatus = (VOID *)0x100015fc;
  original.EnableVbus = (VOID *)0x10001600;
  snapshot = original;
  loaded.ImageBase = (VOID *)0x10000000;
  loaded.ImageSize = scenario == 9 ? 0x14000 : 0x13000;
  BOOT_SERVICES services = { get_protocol, install, connect };
  gBS = &services;
  QCOM_USB_INIT init = { 0 };
  init.StopController = scenario == 11 ? NULL : stop_host;
  BINDING binding = { supported, (VOID *)0x44 };
  EFI_STATUS status = CreatePeripheralController(&init, &binding, &original, 0);
  EFI_STATUS expected[] = {
    EFI_SUCCESS, EFI_UNSUPPORTED, EFI_UNSUPPORTED, EFI_OUT_OF_RESOURCES,
    EFI_DEVICE_ERROR, EFI_DEVICE_ERROR, EFI_UNSUPPORTED, EFI_DEVICE_ERROR,
    EFI_NOT_FOUND, EFI_UNSUPPORTED, EFI_NOT_FOUND, EFI_UNSUPPORTED
  };
  assert(status == expected[scenario]);
  assert(provider_checks == (scenario == 0 || scenario == 8));
  if (scenario == 1 || scenario == 2 || scenario >= 9) {
    assert(allocations == 0 && stops == 0 && published == NULL);
  }
  if (scenario == 3) assert(stops == 0 && published == NULL);
  if (scenario == 4 || scenario == 5) assert(frees == 1 && published == NULL);
  if (scenario == 0 || (scenario >= 6 && scenario <= 8)) {
    assert(frees == 0 && published == allocation);
    assert(((QCOM_USB_CONFIG_INFO *)published)->ModeType == 4);
    assert(original.ModeType == 0x10000);
    free(allocation);
  }
  return 0;
}
"""


class PeripheralPreparationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which("cc")
        if not compiler:
            raise unittest.SkipTest("host C compiler unavailable")
        cls.temporary = tempfile.TemporaryDirectory(prefix="chiron-usb-test-")
        cls.addClassCleanup(cls.temporary.cleanup)
        folder = Path(cls.temporary.name)
        source = (ROOT / "package/ChironCiPkg/ChironHackios.c").read_text()
        types = source[
            source.index("// The vendor DWC3 Supported()"):
            source.index("STATIC EFI_STATUS\nFindFirmwareImage(")
        ]
        function = source[
            source.index("STATIC EFI_STATUS\nCreatePeripheralController("):
            source.index("STATIC EFI_STATUS\nConnectUsbFunction(")
        ]
        unit = folder / "peripheral.c"
        unit.write_text(PRELUDE + types + DOUBLES + function + MAIN)
        cls.program = folder / "peripheral"
        result = subprocess.run([compiler, "-std=c11", "-Wall", "-Wextra", "-Werror",
                                 str(unit), "-o", str(cls.program)],
                                capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(result.stderr)

    def test_success_and_admission_failures(self):
        for scenario in (0, 1, 2, 3, 9, 10, 11):
            with self.subTest(scenario=scenario):
                subprocess.run([str(self.program), str(scenario)], check=True)

    def test_host_stop_and_registration_failures_release_unpublished_copy(self):
        for scenario in (4, 5):
            with self.subTest(scenario=scenario):
                subprocess.run([str(self.program), str(scenario)], check=True)

    def test_binding_failures_retain_registered_storage_and_never_claim_provider(self):
        for scenario in (6, 7, 8):
            with self.subTest(scenario=scenario):
                subprocess.run([str(self.program), str(scenario)], check=True)


if __name__ == "__main__":
    unittest.main()
