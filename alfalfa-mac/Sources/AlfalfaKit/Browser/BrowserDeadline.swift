import Foundation

/// Un callback de WebKit que no responda no deja la cola de peticiones bloqueada para siempre.
@MainActor
enum BrowserDeadline {
    static func run<T>(seconds: TimeInterval,
                       operation: @escaping @MainActor () async throws -> T) async throws -> T {
        let pending = BrowserPending<T>()
        return try await withCheckedThrowingContinuation { continuation in
            pending.continuation = continuation
            pending.work = Task {
                do {
                    pending.finish(.success(try await operation()))
                } catch {
                    pending.finish(.failure(error))
                }
            }
            pending.timer = Task {
                do { try await Task.sleep(nanoseconds: UInt64(seconds * 1_000_000_000)) } catch { return }
                pending.work?.cancel()
                pending.finish(.failure(BrowserFailure("WebKit no respondió al hover dentro del plazo de espera.")))
            }
        }
    }
}

@MainActor
private final class BrowserPending<T> {
    var continuation: CheckedContinuation<T, Error>?
    var work: Task<Void, Never>?
    var timer: Task<Void, Never>?

    func finish(_ result: Result<T, Error>) {
        guard let continuation else { return }
        self.continuation = nil
        timer?.cancel()
        timer = nil
        work = nil
        continuation.resume(with: result)
    }
}
