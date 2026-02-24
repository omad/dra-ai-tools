package main

import (
	"log"
	"os"
)

func main() {
	s := newServer(os.Stdout)
	if err := s.serve(os.Stdin); err != nil {
		log.Fatal(err)
	}
}
