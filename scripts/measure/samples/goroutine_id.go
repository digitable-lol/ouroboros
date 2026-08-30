// Во что обходится узнать номер горутины — единственный способ, какой даёт
// стандартная библиотека Go. Печатает цену одного вызова на разной глубине
// стека, потому что runtime.Stack обходит весь стек, каким бы маленьким ни был
// буфер.
package main

import (
	"fmt"
	"runtime"
	"time"
)

func goroutineID() uint64 {
	var buf [32]byte
	n := runtime.Stack(buf[:], false)
	s := buf[:n]
	const prefix = "goroutine "
	if len(s) < len(prefix) || string(s[:len(prefix)]) != prefix {
		return 0
	}
	var id uint64
	for _, c := range s[len(prefix):] {
		if c < '0' || c > '9' {
			break
		}
		id = id*10 + uint64(c-'0')
	}
	return id
}

func deep(n int, f func()) {
	if n == 0 {
		f()
		return
	}
	deep(n-1, f)
}

func main() {
	const repeats = 30000
	fmt.Println("глубина стека | мкс на один вызов runtime.Stack")
	for _, depth := range []int{0, 10, 30, 100, 300} {
		deep(depth, func() {
			t0 := time.Now()
			for i := 0; i < repeats; i++ {
				_ = goroutineID()
			}
			spent := time.Since(t0)
			fmt.Printf("%13d | %.3f\n", depth,
				float64(spent.Nanoseconds())/float64(repeats)/1000.0)
		})
	}
}
