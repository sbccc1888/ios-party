/* Host substitutions only. Production code is extracted by the Python tool. */
#include <arpa/inet.h>
#include <assert.h>
#include <errno.h>
#include <inttypes.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/mman.h>
#include <sys/types.h>
#include <CommonCrypto/CommonDigest.h>

#include "slide_guard.h"

#define PAGE_SHIFT 12
#define PAGE_SHIFT_4K 12
#define PAGE_SIZE ((size_t)4096)
#define PAGE_MASK (PAGE_SIZE - 1)
#define PAGE_SIZE_FOR_SR_SLIDE 4096
#define PAGE_SIZE_FOR_SR_SLIDE_16KB 16384
#define TRUE 1
#define FALSE 0
#define CRYPTO_SHA2 1
#define MIN(a, b) ((a) < (b) ? (a) : (b))
#define CAST_DOWN(type, value) ((type)(value))
#ifndef __unused
#define __unused __attribute__((unused))
#endif
#define CS_VALIDATE_TAINTED 1
#define CS_VALIDATE_NX 2
#define os_mul_overflow(a, b, c) __builtin_mul_overflow(a, b, c)
#define os_add_overflow(a, b, c) __builtin_add_overflow(a, b, c)
#define os_atomic_inc(p, order) (++*(p))
#define panic(message) do { fprintf(stderr, "%s\n", message); abort(); } while (0)
#define KERN_SUCCESS 0
#define KERN_FAILURE 5
#define KMA_KOBJECT 0
#define KMA_DATA 0
#define KMA_ZERO 0
#define VM_KERN_MEMORY_SECURITY 0
#define Z_WAITOK 0
#define VM_PROT_READ 1
#define UIO_READ 0
#define UIO_SYSSPACE 0
#define kernel_map NULL
#define vfs_context_ucred(x) NULL
#define vfs_context_current() NULL
#define current_proc() NULL
typedef int boolean_t;
typedef int kern_return_t;
typedef uintptr_t vm_offset_t;
typedef size_t vm_size_t;
typedef uint64_t mach_vm_size_t;
typedef uint64_t mach_vm_offset_t;
typedef uint64_t vm_object_offset_t;
typedef uint64_t memory_object_offset_t;
typedef void *memory_object_t;
typedef CC_SHA1_CTX SHA1_CTX;
typedef CC_SHA256_CTX SHA256_CTX;
typedef CC_SHA512_CTX SHA384_CTX;
#define SHA_DIGEST_LENGTH CC_SHA1_DIGEST_LENGTH
#define SHA256_DIGEST_LENGTH CC_SHA256_DIGEST_LENGTH
#define SHA384_DIGEST_LENGTH CC_SHA384_DIGEST_LENGTH

/* CommonCrypto update takes CC_LONG; these wrappers preserve XNU size_t ABI. */
#define DEFINE_HASH(bits) \
static void host_sha##bits##_init(void *ctx) { CC_SHA##bits##_Init(ctx); } \
static void host_sha##bits##_update(void *ctx, const void *data, size_t n) { \
    assert(n <= UINT32_MAX); CC_SHA##bits##_Update(ctx, data, (CC_LONG)n); \
} \
static void host_sha##bits##_final(void *digest, void *ctx) { CC_SHA##bits##_Final(digest, ctx); }
DEFINE_HASH(1)
DEFINE_HASH(256)
DEFINE_HASH(384)
#define SHA1Init host_sha1_init
#define SHA1Update host_sha1_update
#define SHA1Final host_sha1_final
#define SHA256_Init host_sha256_init
#define SHA256_Update host_sha256_update
#define SHA256_Final host_sha256_final
#define SHA384_Init host_sha384_init
#define SHA384_Update host_sha384_update
#define SHA384_Final host_sha384_final

struct host_vnode {
    uint8_t *data;
    size_t size;
    off_t base;
};
typedef struct host_vnode *vnode_t;
