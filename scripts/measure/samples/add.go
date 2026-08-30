// Пример для замера: сложение и функция, которая паникует.
package main

import (
	"fmt"
	"os"
	"strconv"
)

func add(a, b int) int { return a + b }

func boom() int { panic("так задумано") }

func main() {
	n := 20000
	if len(os.Args) > 1 {
		if v, err := strconv.Atoi(os.Args[1]); err == nil {
			n = v
		}
	}
	total := 0
	for i := 0; i < n; i++ {
		total = add(total, i)
	}
	func() {
		defer func() { _ = recover() }()
		boom()
	}()
	fmt.Println("итог", total)
}
