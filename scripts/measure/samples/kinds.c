/* Разные виды возврата — что C может записать, а что нет. */
#include <stdio.h>

struct point { int x, y; };

static struct point make(int x, int y)
{
	struct point p = { x, y };
	return p;
}

static void nothing(int n)
{
	(void)n;
}

static double half(double v)
{
	return v / 2.0;
}

static unsigned char byte(unsigned char c)
{
	return c;
}

int main(void)
{
	struct point p = make(1, 2);
	nothing(p.x);
	printf("%d %f %u\n", p.y, half(5.0), byte(7));
	return 0;
}
