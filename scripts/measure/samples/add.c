#include <stdio.h>
#include <stdlib.h>

static long add(long a, long b)
{
	return a + b;
}

static const char *name(const char *who)
{
	return who;
}

int main(int argc, char **argv)
{
	long n = argc > 1 ? atol(argv[1]) : 20000;
	long total = 0;
	long i;

	for (i = 0; i < n; i++)
		total = add(total, i);
	printf("%s %ld\n", name("итог"), total);
	return 0;
}
