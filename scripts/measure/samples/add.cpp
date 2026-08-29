#include <cstdio>
#include <cstdlib>
#include <stdexcept>
#include <string>

namespace demo {

struct Point {
	int x, y;
};

long add(long a, long b)
{
	return a + b;
}

Point make(int x, int y)
{
	return Point{x, y};
}

double div(double a, double b)
{
	if (b == 0.0)
		throw std::runtime_error("деление на ноль");
	return a / b;
}

}  // namespace demo

int main(int argc, char **argv)
{
	long n = argc > 1 ? std::atol(argv[1]) : 20000;
	long total = 0;

	for (long i = 0; i < n; i++)
		total = demo::add(total, i);
	demo::Point p = demo::make(1, 2);
	try {
		demo::div(1.0, 0.0);
	} catch (const std::exception &) {
	}
	std::printf("%ld %d\n", total, p.y);
	return 0;
}
