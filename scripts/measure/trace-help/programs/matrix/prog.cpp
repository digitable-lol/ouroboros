// Обход матрицы: пропуск отрицательных, вес по клетке, ограничение сверху.
#include <cstdio>
#include <vector>

int weight(int row, int col) {
    return (row + col) % 3;
}

int clamp_value(int v) {
    if (v > 100) {
        return 100;
    }
    if (v < 0) {
        return 0;
    }
    return v;
}

int skip(int v) {
    return v < 0;
}

int contribute(int v, int w) {
    return clamp_value(v) * w;
}

int overflow_guard(int total) {
    return total > 1000000 ? -1 : total;
}

int reduce_matrix(const std::vector<std::vector<int>> &m) {
    int total = 0;
    for (size_t r = 0; r < m.size(); r++) {
        for (size_t c = 0; c < m[r].size(); c++) {
            int v = m[r][c];
            if (skip(v)) {
                continue;
            }
            int w = weight((int)r, (int)c);
            total += contribute(v, w);
        }
    }
    return overflow_guard(total);
}

int main() {
    std::vector<std::vector<int>> m = {{5, -2, 300}, {7, 4, -1}};
    printf("свёртка %d\n", reduce_matrix(m));
    return 0;
}
