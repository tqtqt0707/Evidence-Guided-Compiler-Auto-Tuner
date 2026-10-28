#include <errno.h>
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static uint64_t next_u64(uint64_t *state) {
    uint64_t z = (*state += UINT64_C(0x9e3779b97f4a7c15));
    z = (z ^ (z >> 30)) * UINT64_C(0xbf58476d1ce4e5b9);
    z = (z ^ (z >> 27)) * UINT64_C(0x94d049bb133111eb);
    return z ^ (z >> 31);
}

static uint64_t hash_u64(uint64_t hash, uint64_t value) {
    for (unsigned shift = 0; shift < 64; shift += 8) {
        hash ^= (value >> shift) & UINT64_C(0xff);
        hash *= UINT64_C(1099511628211);
    }
    return hash;
}

static int parse_u64(const char *text, uint64_t *out) {
    char *end = NULL;
    errno = 0;
    if (text[0] == '-' || text[0] == '\0') return 0;
    unsigned long long value = strtoull(text, &end, 10);
    if (errno || *end != '\0') return 0;
    *out = (uint64_t)value;
    return 1;
}

int main(int argc, char **argv) {
    uint64_t size = 0, seed = 0, repeat = 0;
    if (argc != 7 || strcmp(argv[1], "--size") || strcmp(argv[3], "--seed") ||
        strcmp(argv[5], "--repeat") || !parse_u64(argv[2], &size) ||
        !parse_u64(argv[4], &seed) || !parse_u64(argv[6], &repeat) ||
        size == 0 || size > UINT64_C(16000000) || repeat == 0 || repeat > 100 ||
        size > SIZE_MAX / sizeof(uint32_t)) {
        fputs("usage: histogram --size 1..16000000 --seed N --repeat 1..100\n", stderr);
        return 2;
    }
    uint32_t *values = malloc((size_t)size * sizeof(*values));
    if (!values) {
        fputs("allocation failed\n", stderr);
        return 3;
    }
    uint64_t state = seed;
    for (uint64_t i = 0; i < size; ++i) values[i] = (uint32_t)next_u64(&state);
    uint64_t hash = UINT64_C(14695981039346656037);
    for (uint64_t r = 0; r < repeat; ++r) {
        uint64_t bins[256] = {0};
        values[r % size] ^= (uint32_t)(r + 1);
        for (uint64_t i = 0; i < size; ++i) {
            uint32_t value = values[i];
            uint32_t bin;
            if (value & UINT32_C(1)) {
                bin = (value >> 8) & UINT32_C(255);
            } else if (value & UINT32_C(2)) {
                bin = (value >> 16) & UINT32_C(255);
            } else {
                bin = value & UINT32_C(255);
            }
            ++bins[bin];
        }
        uint64_t total = 0;
        for (unsigned i = 0; i < 256; ++i) {
            total += bins[i];
            hash = hash_u64(hash, bins[i]);
        }
        if (total != size) {
            fputs("histogram count mismatch\n", stderr);
            free(values);
            return 4;
        }
    }
    printf("checksum:%016" PRIx64 "\n", hash);
    free(values);
    return 0;
}
