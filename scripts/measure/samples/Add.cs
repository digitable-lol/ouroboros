using System;

class Add
{
	static long Sum(long a, long b)
	{
		return a + b;
	}

	static double Div(double a, double b)
	{
		if (b == 0.0)
			throw new DivideByZeroException("деление на ноль");
		return a / b;
	}

	static long Run(int n)
	{
		long total = 0;

		for (int i = 0; i < n; i++)
			total = Sum(total, i);
		try {
			Div(1, 0);
		} catch (DivideByZeroException) {
			// проглочено намеренно
		}
		return total;
	}

	static void Main(string[] args)
	{
		int n = args.Length > 0 ? int.Parse(args[0]) : 20000;

		Console.WriteLine(Run(n));
	}
}
