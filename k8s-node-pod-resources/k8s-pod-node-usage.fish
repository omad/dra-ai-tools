# Defined via `source`
function k8s-live-pods --description 'Live resource usage on a node' --argument-names NODE

    if test -z "$NODE"
        echo "Usage: k8s-live-pods NODE" >&2
        return 1
    end

    set -l pods (
        kubectl get pods -A \
            --field-selector spec.nodeName="$NODE" \
            -o=jsonpath='{range .items[*]}{.metadata.namespace}{","}{.metadata.name}{"\n"}{end}'
    )

    if test (count $pods) -eq 0
        echo "No pods found on node $NODE" >&2
        return 0
    end

    begin
        echo "namespace,pod,container,cpu,memory"
        printf '%s\n' $pods \
            | xargs -P 8 -n 1 sh -c '
                pod_ref="$1"
                ns="${pod_ref%%,*}"
                name="${pod_ref#*,}"

                kubectl top pod -n "$ns" "$name" --containers --no-headers 2>/dev/null \
                    | awk -v ns="$ns" "NF >= 4 { print ns \",\" \$1 \",\" \$2 \",\" \$3 \",\" \$4 }"
            ' sh \
            | sort -t, -k1,1 -k2,2 -k3,3
    end | uvx --from rich-cli rich --csv -
end

function k8s-live-pods-with-resources --description 'Live resource usage plus requests and limits on a node' --argument-names NODE

    if test -z "$NODE"
        echo "Usage: k8s-live-pods-with-resources NODE" >&2
        return 1
    end

    set -l script (dirname (status filename))/k8s_pod_node_resources.py
    uv run "$script" "$NODE"
end
