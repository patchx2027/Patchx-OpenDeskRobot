/* Finite userspace encoder. Full-range BT.601 is an explicit display assumption. */
#include <errno.h>
#include <fcntl.h>
#include <math.h>
#include <setjmp.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include <jerror.h>
#include <jpeglib.h>

struct context {
    struct jpeg_compress_struct jpeg;
    struct jpeg_error_mgr error;
    struct jpeg_destination_mgr destination;
    jmp_buf jump;
    unsigned char* encoded;
    size_t capacity;
    size_t length;
    int created;
};

static void failed(j_common_ptr info) {
    struct context* ctx = (struct context*)info->client_data;
    longjmp(ctx->jump, 1);
}

static void init_destination(j_compress_ptr info) {
    struct context* ctx = (struct context*)info->client_data;
    ctx->destination.next_output_byte = ctx->encoded;
    ctx->destination.free_in_buffer = ctx->capacity;
}

static boolean full_destination(j_compress_ptr info) {
    ERREXIT(info, JERR_BUFFER_SIZE);
    return FALSE;
}

static void finish_destination(j_compress_ptr info) {
    struct context* ctx = (struct context*)info->client_data;
    ctx->length = ctx->capacity - ctx->destination.free_in_buffer;
}

static unsigned char clamp(double value) {
    return (unsigned char)(value < 0 ? 0 : value > 255 ? 255 : floor(value + 0.5));
}

static int encode(struct context* ctx,
                  const unsigned char* nv12,
                  unsigned char* row,
                  unsigned width,
                  unsigned height,
                  unsigned stride,
                  int quality) {
    memset(&ctx->jpeg, 0, sizeof(ctx->jpeg));
    ctx->jpeg.err = jpeg_std_error(&ctx->error);
    ctx->error.error_exit = failed;
    ctx->jpeg.client_data = ctx;
    ctx->created = 0;
    if (setjmp(ctx->jump)) {
        if (ctx->created) {
            jpeg_destroy_compress(&ctx->jpeg);
        }
        return -1;
    }
    ctx->created = 1;
    jpeg_create_compress(&ctx->jpeg);
    ctx->destination.init_destination = init_destination;
    ctx->destination.empty_output_buffer = full_destination;
    ctx->destination.term_destination = finish_destination;
    ctx->jpeg.dest = &ctx->destination;
    ctx->jpeg.image_width = width;
    ctx->jpeg.image_height = height;
    ctx->jpeg.input_components = 3;
    ctx->jpeg.in_color_space = JCS_RGB;
    jpeg_set_defaults(&ctx->jpeg);
    jpeg_set_quality(&ctx->jpeg, quality, TRUE);
    ctx->jpeg.optimize_coding = TRUE;
    jpeg_start_compress(&ctx->jpeg, TRUE);
    while (ctx->jpeg.next_scanline < height) {
        unsigned y = ctx->jpeg.next_scanline;
        const unsigned char* luma = nv12 + (size_t)y * stride;
        const unsigned char* chroma =
            nv12 + (size_t)height * stride + (size_t)(y / 2) * stride;
        for (unsigned x = 0; x < width; ++x) {
            double u = (double)chroma[x & ~1U] - 128.0;
            double v = (double)chroma[(x & ~1U) + 1] - 128.0;
            row[3 * x] = clamp(luma[x] + 1.402 * v);
            row[3 * x + 1] = clamp(luma[x] - 0.344136 * u - 0.714136 * v);
            row[3 * x + 2] = clamp(luma[x] + 1.772 * u);
        }
        JSAMPROW rows[1] = {row};
        if (jpeg_write_scanlines(&ctx->jpeg, rows, 1) != 1) {
            ERREXIT(&ctx->jpeg, JERR_BUFFER_SIZE);
        }
    }
    jpeg_finish_compress(&ctx->jpeg);
    jpeg_destroy_compress(&ctx->jpeg);
    ctx->created = 0;
    return 0;
}

static unsigned number(const char* text) {
    if (!text || !*text) {
        return 0;
    }
    for (const char* p = text; *p; ++p) {
        if (*p < '0' || *p > '9') {
            return 0;
        }
    }
    errno = 0;
    char* end = NULL;
    unsigned long value = strtoul(text, &end, 10);
    return errno || !end || *end || value > 65536 ? 0 : (unsigned)value;
}

static int write_all(int fd, const unsigned char* data, size_t size) {
    size_t done = 0;
    while (done < size) {
        ssize_t written = write(fd, data + done, size - done);
        if (written < 0 && errno == EINTR) {
            continue;
        }
        if (written <= 0) {
            return -1;
        }
        done += (size_t)written;
    }
    return 0;
}

int main(int argc, char** argv) {
    const char *input = NULL, *output = NULL;
    unsigned width = 0, height = 0, stride = 0, budget = 0;
    for (int i = 1; i < argc; i += 2) {
        if (i + 1 >= argc) {
            return 2;
        }
        const char *key = argv[i], *value = argv[i + 1];
        if (strcmp(key, "--input") == 0) {
            input = value;
        } else if (strcmp(key, "--output") == 0) {
            output = value;
        } else if (strcmp(key, "--width") == 0) {
            width = number(value);
        } else if (strcmp(key, "--height") == 0) {
            height = number(value);
        } else if (strcmp(key, "--stride") == 0) {
            stride = number(value);
        } else if (strcmp(key, "--max-bytes") == 0) {
            budget = number(value);
        } else {
            return 2;
        }
    }
    if (!input || !output || *input != '/' || *output != '/' || width < 2 ||
        height < 2 || width > 4096 || height > 4096 || stride < width ||
        stride > 8192 || (width | height | stride) & 1 || budget < 128 ||
        budget > 65536) {
        return 2;
    }
    size_t raw_size = (size_t)stride * height * 3 / 2;
    int fd = open(input, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    struct stat info;
    if (fd < 0) {
        return 3;
    }
    if (fstat(fd, &info) || !S_ISREG(info.st_mode) ||
        info.st_size != (off_t)raw_size) {
        close(fd);
        return 3;
    }
    unsigned char* raw = malloc(raw_size);
    unsigned char* row = malloc((size_t)width * 3);
    struct context* ctx = calloc(1, sizeof(*ctx));
    if (!raw || !row || !ctx) {
        close(fd);
        free(raw);
        free(row);
        free(ctx);
        return 4;
    }
    ctx->capacity = (size_t)width * height * 3 + 65536;
    ctx->encoded = malloc(ctx->capacity);
    int result = 4;
    size_t have = 0;
    while (have < raw_size) {
        ssize_t got = read(fd, raw + have, raw_size - have);
        if (got < 0 && errno == EINTR) {
            continue;
        }
        if (got <= 0) {
            break;
        }
        have += (size_t)got;
    }
    unsigned char extra;
    ssize_t tail = read(fd, &extra, 1);
    int closed = close(fd);
    if (have != raw_size || tail != 0 || closed || !ctx->encoded) {
        goto done;
    }
    int quality = 90;
    for (; quality >= 10; quality -= 10) {
        if (encode(ctx, raw, row, width, height, stride, quality)) {
            goto done;
        }
        if (ctx->length <= budget) {
            break;
        }
    }
    if (quality < 10) {
        result = 5;
        goto done;
    }
    fd = open(output, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600);
    if (fd < 0) {
        result = 6;
        goto done;
    }
    int wrote = write_all(fd, ctx->encoded, ctx->length);
    int synced = fsync(fd);
    closed = close(fd);
    if (wrote || synced || closed) {
        unlink(output);
        result = 6;
        goto done;
    }
    printf(
        "{\"ok\":true,\"width\":%u,\"height\":%u,\"jpeg_bytes\":%zu,\"quality\":%d,"
        "\"matrix\":\"full-range BT601 assumed\"}\n",
        width,
        height,
        ctx->length,
        quality);
    result = 0;
done:
    free(ctx->encoded);
    free(ctx);
    free(row);
    free(raw);
    return result;
}
