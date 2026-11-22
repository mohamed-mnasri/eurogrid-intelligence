// Hash routes: #/<page>/<optional parameter>, e.g. #/investigations/anm_0123456789abcdef
export function navigate(page: string, param?: string) {
  window.location.hash = `#/${page}${param ? `/${param}` : ''}`
}
