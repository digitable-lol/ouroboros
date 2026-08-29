// Возврат списком в фигурных скобках: раньше на нём ломалась сборка.
#include <cstdio>
#include <vector>

std::vector<int> three()
{
	return {1, 2, 3};
}

int main()
{
	auto v = three();
	std::printf("%zu\n", v.size());
	return 0;
}
