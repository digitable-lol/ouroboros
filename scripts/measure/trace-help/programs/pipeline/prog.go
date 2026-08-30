// Конвейер: разбор записей, отсев, свёртка.
package main

import (
	"fmt"
	"strconv"
	"strings"
)

func parseRecord(raw string) (string, int, bool) {
	parts := strings.Split(raw, "=")
	if len(parts) != 2 {
		return "", 0, false
	}
	n, err := strconv.Atoi(parts[1])
	if err != nil {
		return parts[0], 0, false
	}
	return parts[0], n, true
}

func keep(name string, value int) bool {
	return value > 0 && !strings.HasPrefix(name, "_")
}

func scale(value int) int {
	return value * 2
}

func warn(raw string) int {
	fmt.Println("пропущено:", raw)
	return 0
}

func median(values []int) int {
	if len(values) == 0 {
		return 0
	}
	return values[len(values)/2]
}

func summarize(total int, count int) int {
	if count == 0 {
		return 0
	}
	return total / count
}

func run(raws []string) int {
	total, count := 0, 0
	for _, raw := range raws {
		name, value, ok := parseRecord(raw)
		if !ok {
			warn(raw)
			continue
		}
		if !keep(name, value) {
			continue
		}
		total += scale(value)
		count++
	}
	return summarize(total, count)
}

func main() {
	raws := []string{"a=5", "b=abc", "_c=7", "d=-2", "e=10", "broken"}
	fmt.Println("среднее", run(raws))
}
