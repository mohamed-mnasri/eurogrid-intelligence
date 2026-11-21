// Keeps one broken page from blanking the whole app; shows the error and lets the user retry.
import { Component, type ReactNode } from 'react'

type Props = { children: ReactNode; resetKey: string }
type State = { error: Error | null; key: string }

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null, key: this.props.resetKey }

  static getDerivedStateFromError(error: Error) {
    return { error }
  }

  static getDerivedStateFromProps(props: Props, state: State) {
    // Navigating to another page clears the error.
    return props.resetKey !== state.key ? { error: null, key: props.resetKey } : null
  }

  render() {
    if (!this.state.error) return this.props.children
    return <main className="dashboard"><div className="state state-error" role="alert"><b>This page hit an unexpected error.</b> {this.state.error.message}
      <span className="state-hint">The other pages still work. Reload to try again; if it repeats, the API returned data this page did not expect.</span>
      <button className="button small" onClick={() => this.setState({ error: null })}>Try again</button></div></main>
  }
}
