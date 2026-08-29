# programming languages

## cpp

### input

```cpp
int sum(int a, int b) {
    return a + b;
}
```

### output

```cpp
int sum(int a, int b) {
    int __result;
    // Лямбда-деструктор (аналог finally) связывается с __result по ссылке
    auto __finally = AtScopeExit([&]() {
        std::cout << "Exit sum. Result: " << __result << "\n";
    });

    std::cout << "Enter sum, Args: " << a << ", " << b << "\n";
    
    return (__result = a + b); // Запись в __result происходит до вызова деструктора
}
```

## js

### input

```js
function sum(a: number, b: number) {
    return a + b;
}
```

### output

```js
function sum(a: number, b: number) {
    console.group("sum");
    console.log("Args:", [a, b]);
    let __result;
    try {
        return (__result = a + b); // Перехватываем возвращаемое значение
    } finally {
        console.log("Result:", __result);
        console.groupEnd();
    }
}
```

## csharp

### input

```csharp
int Sum(int a, int b) {
    return a + b;
}
```

### output 

```csharp
int Sum(int a, int b) {
    Console.WriteLine($"Enter Sum with {a}, {b}");
    int __result = default;
    try {
        // Экзекутор разбивает 'return a + b;' на две строки:
        __result = a + b; 
        return __result;
    } finally {
        Console.WriteLine($"Exit Sum. Result: {__result}");
    }
}
```
