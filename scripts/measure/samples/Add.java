public class Add {
	static long add(long a, long b)
	{
		return a + b;
	}

	static double div(double a, double b)
	{
		if (b == 0.0)
			throw new ArithmeticException("деление на ноль");
		return a / b;
	}

	static long run(int n)
	{
		long total = 0;

		for (int i = 0; i < n; i++)
			total = add(total, i);
		try {
			div(1, 0);
		} catch (ArithmeticException e) {
			// проглочено намеренно
		}
		return total;
	}

	public static void main(String[] args)
	{
		int n = args.length > 0 ? Integer.parseInt(args[0]) : 20000;

		System.out.println(run(n));
	}
}
