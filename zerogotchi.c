#define _POSIX_C_SOURCE 200809L

#include <errno.h>
#include <fcntl.h>
#include <linux/fb.h>
#include <linux/input.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>

enum { WIDTH = 320, HEIGHT = 170 };
static uint16_t pixels[HEIGHT][WIDTH];

static const uint8_t font[128][7] = {
    ['!'] = {4, 4, 4, 4, 4, 0, 4},
    ['A'] = {14, 17, 17, 31, 17, 17, 17},
    ['C'] = {14, 17, 16, 16, 16, 17, 14},
    ['D'] = {30, 17, 17, 17, 17, 17, 30},
    ['E'] = {31, 16, 16, 30, 16, 16, 31},
    ['G'] = {14, 17, 16, 23, 17, 17, 15},
    ['H'] = {17, 17, 17, 31, 17, 17, 17},
    ['I'] = {14, 4, 4, 4, 4, 4, 14},
    ['L'] = {16, 16, 16, 16, 16, 16, 31},
    ['O'] = {14, 17, 17, 17, 17, 17, 14},
    ['P'] = {30, 17, 17, 30, 16, 16, 16},
    ['R'] = {30, 17, 17, 30, 20, 18, 17},
    ['S'] = {15, 16, 16, 14, 1, 1, 30},
    ['T'] = {31, 4, 4, 4, 4, 4, 4},
    ['W'] = {17, 17, 17, 21, 21, 21, 10},
    ['X'] = {17, 17, 10, 4, 10, 17, 17},
    ['Z'] = {31, 1, 2, 4, 8, 16, 31},
};

static void rect(int x, int y, int width, int height, uint16_t color)
{
    for (int row = y; row < y + height; row++)
        for (int col = x; col < x + width; col++)
            pixels[row][col] = color;
}

static void text_centered(const char *text, int y, int scale, uint16_t color)
{
    int x = (WIDTH - ((int)strlen(text) * 6 - 1) * scale) / 2;
    for (; *text; text++, x += 6 * scale) {
        const uint8_t *glyph = font[(unsigned char)*text];
        for (int row = 0; row < 7; row++)
            for (int col = 0; col < 5; col++)
                if (glyph[row] & (16 >> col))
                    rect(x + col * scale, y + row * scale, scale, scale, color);
    }
}

static int show_screen(int fb)
{
    struct fb_var_screeninfo mode;
    struct fb_fix_screeninfo layout;
    if (ioctl(fb, FBIOGET_VSCREENINFO, &mode) < 0 ||
        ioctl(fb, FBIOGET_FSCREENINFO, &layout) < 0) {
        perror("Read LCD geometry");
        return 1;
    }
    if (mode.xres != WIDTH || mode.yres != HEIGHT || mode.bits_per_pixel != 16 ||
        mode.red.offset != 11 || mode.red.length != 5 ||
        mode.green.offset != 5 || mode.green.length != 6 ||
        mode.blue.offset != 0 || mode.blue.length != 5 ||
        layout.line_length < (mode.xoffset + WIDTH) * sizeof(uint16_t)) {
        fputs("Expected a 320x170 RGB565 Cardputer Zero LCD\n", stderr);
        return 1;
    }

    rect(0, 0, WIDTH, HEIGHT, 0x10e5);
    text_centered("ZEROGOTCHI", 18, 2, 0x77f8);
    rect(72, 44, 176, 2, 0x33cd);
    text_centered("HELLO WORLD!", 76, 4, 0xffff);
    text_centered("ESC TO EXIT", 133, 2, 0xa536);

    for (int row = 0; row < HEIGHT; row++) {
        off_t offset = (off_t)(mode.yoffset + row) * layout.line_length +
                       (off_t)mode.xoffset * sizeof(uint16_t);
        if (pwrite(fb, pixels[row], sizeof(pixels[row]), offset) != sizeof(pixels[row])) {
            fputs("Could not write a full row to the LCD\n", stderr);
            return 1;
        }
    }
    return 0;
}

static int wait_for_escape(int keyboard)
{
    struct input_event event;
    for (;;) {
        ssize_t bytes = read(keyboard, &event, sizeof(event));
        if (bytes < 0 && errno == EINTR)
            continue;
        if (bytes != sizeof(event)) {
            fputs("Could not read an event from the built-in keyboard\n", stderr);
            return 1;
        }
        if (event.type == EV_KEY && event.code == KEY_ESC && event.value == 1)
            return 0;
    }
}

int main(void)
{
    int keyboard = open("/dev/input/cardputer-zero-internal", O_RDONLY);
    if (keyboard < 0) {
        perror("Open built-in keyboard");
        return 1;
    }
    int lcd = open("/dev/fb_lcd", O_WRONLY);
    if (lcd < 0) {
        perror("Open built-in LCD");
        close(keyboard);
        return 1;
    }
    int result = show_screen(lcd);
    close(lcd);
    if (result == 0)
        result = wait_for_escape(keyboard);
    close(keyboard);
    return result;
}
