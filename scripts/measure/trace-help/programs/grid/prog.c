/* Ходы по полю: проверка границ, занятость клетки, счёт поставленных. */
#include <stdio.h>

int in_bounds(int x, int y, int w, int h) {
    return x >= 0 && y >= 0 && x < w && y < h;
}

int cell_index(int x, int y, int w) {
    return y * w + x;
}

int occupy(int *field, int idx) {
    if (field[idx] != 0) {
        return 0;
    }
    field[idx] = 1;
    return 1;
}

int reject(int x, int y) {
    printf("вне поля %d %d\n", x, y);
    return 0;
}

int clear_all(int *field, int n) {
    for (int i = 0; i < n; i++) {
        field[i] = 0;
    }
    return n;
}

int play(int *field, int w, int h, int count, int *moves) {
    int placed = 0;
    for (int i = 0; i < count; i += 2) {
        int x = moves[i];
        int y = moves[i + 1];
        if (!in_bounds(x, y, w, h)) {
            reject(x, y);
            continue;
        }
        placed += occupy(field, cell_index(x, y, w));
    }
    return placed;
}

int main(void) {
    int field[9] = {0};
    int moves[12] = {0, 0, 1, 1, 0, 0, 3, 1, 2, 2, -1, 0};
    printf("поставлено %d\n", play(field, 3, 3, 12, moves));
    return 0;
}
