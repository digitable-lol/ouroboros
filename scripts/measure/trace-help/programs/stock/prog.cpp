// Склад: резерв под заявки, штраф за недостачу.
#include <cstdio>
#include <vector>

int available(int on_hand, int reserved) {
    return on_hand - reserved;
}

int reserve(int on_hand, int reserved, int want) {
    if (want <= available(on_hand, reserved)) {
        return want;
    }
    return 0;
}

int backorder(int want, int got) {
    return want - got;
}

int penalty(int missing) {
    return missing * 10;
}

int restock(int on_hand, int add) {
    return on_hand + add;
}

int process(const std::vector<int> &wants, int on_hand) {
    int reserved = 0;
    int lost = 0;
    for (size_t i = 0; i < wants.size(); i++) {
        int got = reserve(on_hand, reserved, wants[i]);
        reserved += got;
        if (got == 0) {
            lost += penalty(backorder(wants[i], got));
        }
    }
    printf("зарезервировано %d, штраф %d\n", reserved, lost);
    return reserved;
}

int main() {
    std::vector<int> wants = {4, 3, 5, 2};
    process(wants, 10);
    return 0;
}
